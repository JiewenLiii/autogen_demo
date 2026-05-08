import os
import re
import time
import json
import warnings
import string
from datetime import datetime
from dotenv import load_dotenv
import autogen
from openai import APIError, APIConnectionError, RateLimitError

# ===================== 全局配置 =====================
warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", category=UserWarning, module="flaml")

# 计费单价：DeepSeek-V4-Flash 元/千Token
PRICE_INPUT_PER_K = 0.0005
PRICE_OUTPUT_PER_K = 0.0015

# 模型参数
MODEL_NAME = "deepseek-v4-flash"
TEMPERATURE = 0.0
MAX_TOKENS = 4096
API_TIMEOUT = 180
MAX_RETRY = 3

# ANSI 颜色
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
RESET = "\033[0m"

# 编号持久化文件
COUNTER_FILE = "task_counter.json"

# 全局统计
total_cost_all = 0.0
total_input_tokens = 0
total_output_tokens = 0

# 会话上下文记忆
session_history = []
current_task_id = 0
current_task_simple_name = ""
current_task_type = ""
# 标记：是否需要生成保存文件
need_save_file = False

# ===================== 目录初始化 =====================
load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "agent_output")
CODE_DIR = os.path.join(OUTPUT_DIR, "codes")
DOC_DIR = os.path.join(OUTPUT_DIR, "docs")
LOG_DIR = os.path.join(OUTPUT_DIR, "logs")
REPORT_DIR = os.path.join(OUTPUT_DIR, "reports")

# 只在真正要存文件时才创建目录
def init_dirs():
    os.makedirs(CODE_DIR, exist_ok=True)
    os.makedirs(DOC_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

# ===================== 任务编号持久化 =====================
def load_task_counter() -> int:
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("last_task_id", 0)
    return 0

def save_task_counter(num: int):
    with open(COUNTER_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_task_id": num}, f, ensure_ascii=False, indent=2)

# ===================== LLM配置 =====================
LLM_CONFIG = {
    "config_list": [{
        "model": MODEL_NAME,
        "api_key": DEEPSEEK_API_KEY,
        "base_url": "https://api.deepseek.com/v1",
        "price": [PRICE_INPUT_PER_K, PRICE_OUTPUT_PER_K]
    }],
    "temperature": TEMPERATURE,
    "max_tokens": MAX_TOKENS,
    "timeout": API_TIMEOUT,
}

# ===================== 智能体 =====================
requirement_agent = autogen.ConversableAgent(
    name="RequirementAnalyzer",
    system_message="""
1. 先判断用户是否要求【保存/导出/生成文件/存为文档】，标记是否需要落地存文件
2. 全新任务区分：【编程任务】或【文案任务】
   - 函数、算法、代码、编程 → 【编程任务】
   - 作文、总结、演讲稿、翻译、攻略、作业 → 【文案任务】
3. 结合上下文精简提炼需求40字以内，只输出标识+核心需求
""",
    llm_config=LLM_CONFIG,
    max_consecutive_auto_reply=1,
)

content_agent = autogen.ConversableAgent(
    name="ContentGenerator",
    system_message="""
结合上下文生成内容：
编程任务：输出规范可运行Python代码，用```python ```包裹。
文案任务：输出结构完整、语言通顺的正文内容。
迭代指令：基于上一轮内容直接优化修改，不重复赘述。
直接输出结果，不要开场白、多余解释。
""",
    llm_config=LLM_CONFIG,
    max_consecutive_auto_reply=1,
)

review_agent = autogen.ConversableAgent(
    name="Reviewer",
    system_message="""
编程任务：生成标准pytest测试用例，代码块包裹。
文案任务：对原文润色优化、理顺逻辑、修正语句。
迭代指令：按要求做对应调整完善。
只输出最终内容，无多余文字。
""",
    llm_config=LLM_CONFIG,
    max_consecutive_auto_reply=1,
)

user_proxy = autogen.UserProxyAgent(
    name="SystemController",
    human_input_mode="NEVER",
    code_execution_config=False,
    max_consecutive_auto_reply=0,
)

# ===================== 工具函数 =====================
def calc_cost(input_tokens: int, output_tokens: int) -> float:
    in_cost = input_tokens / 1000 * PRICE_INPUT_PER_K
    out_cost = output_tokens / 1000 * PRICE_OUTPUT_PER_K
    return round(in_cost + out_cost, 6)

def estimate_token_count(text: str) -> int:
    cn_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
    en_words = len(re.findall(r'[a-zA-Z0-9]+', text))
    return int(cn_chars * 1.3 + en_words)

def extract_python_code(text: str) -> str:
    pattern = r"```python\s*(.*?)\s*```"
    match = re.search(pattern, text, re.DOTALL)
    code = match.group(1).strip() if match else text.strip()
    lines = [line.rstrip() for line in code.splitlines() if line.strip()]
    return "\n".join(lines)

def clean_filename(task_text: str) -> str:
    drop_words = ["编写","实现","写一个","请设计","Python函数","函数","算法","开发","完成"]
    text = task_text.strip()
    for w in drop_words:
        text = text.replace(w, "")
    illegal = r'[\\/:*?"<>|，。！？；：""''（）【】《》、 ]'
    text = re.sub(illegal, "", text)
    if len(text) > 12:
        text = text[:12]
    return text

def get_task_type(req_text: str) -> str:
    if "【编程任务】" in req_text:
        return "code"
    return "doc"

def check_need_save(user_text: str) -> bool:
    """检测是否要求保存文件"""
    save_key = ["保存","导出","生成文件","存为文档","存档","写入文件"]
    for k in save_key:
        if k in user_text:
            return True
    return False

def save_file(path: str, content: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def api_retry(func, max_retries=MAX_RETRY):
    for i in range(max_retries):
        try:
            return func()
        except (APIConnectionError, RateLimitError):
            delay = 2 ** i
            print(f"{YELLOW}⚠️  接口异常，{delay}s后重试 ({i+1}/{max_retries}){RESET}")
            time.sleep(delay)
    return func()

def validate_code(code: str) -> tuple[bool, str]:
    try:
        compile(code, "<string>", "exec")
        return True, f"{GREEN}✅ 代码语法校验通过{RESET}"
    except SyntaxError as e:
        return False, f"{RED}❌ 语法错误：第{e.lineno}行 {e.msg}{RESET}"
    except Exception as e:
        return False, f"{RED}❌ 校验失败：{str(e)}{RESET}"

def save_task_log(task_id: int, task_name: str, task: str, status: str, msg: str,
                  in_tok: int, out_tok: int, cost: float, cost_time: float, task_type: str):
    log = {
        "task_id": task_id,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "task_type": "编程任务" if task_type=="code" else "文案任务",
        "task_name": task_name,
        "task_content": task,
        "status": status,
        "message": msg,
        "cost_time_sec": round(cost_time, 2),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "task_cost_yuan": cost,
        "price_rule": f"输入:{PRICE_INPUT_PER_K}/千Token 输出:{PRICE_OUTPUT_PER_K}/千Token"
    }
    log_path = os.path.join(LOG_DIR, f"task_{task_id}_{task_name}_log.json")
    save_file(log_path, json.dumps(log, ensure_ascii=False, indent=2))

def generate_markdown_report(task_id: int, task_name: str, task: str, req: str,
                            content1: str, content2: str, task_type: str,
                            in_tok: int, out_tok: int, cost: float, all_cost: float, cost_time: float):
    report = "# 多智能体任务报告\n"
    report += f"**任务类型**：{'编程开发任务' if task_type=='code' else '文案创作任务'}\n"
    report += "**生成时间**：" + datetime.now().strftime('%Y-%m-%d %H:%M:%S') + "\n"
    report += "**任务耗时**：" + str(round(cost_time, 2)) + " 秒\n\n"
    report += "## 一、原始需求\n" + task + "\n\n"
    report += "## 二、需求解析\n" + req + "\n\n"
    report += "## 三、生成内容\n" + content1 + "\n\n"
    report += "## 四、优化/测试内容\n" + content2 + "\n\n"
    report += "## 五、Token与费用统计\n"
    report += "| 项目 | 数值 |\n|------|------|\n"
    report += f"| 输入Token | {in_tok} |\n"
    report += f"| 输出Token | {out_tok} |\n"
    report += f"| 总Token | {in_tok + out_tok} |\n"
    report += f"| 本次任务费用 | {cost:.6f} 元 |\n"
    report += f"| 程序累计总花费 | {all_cost:.6f} 元 |\n"

    report_path = os.path.join(REPORT_DIR, f"task_{task_id}_{task_name}_report.md")
    save_file(report_path, report)
    return report_path

# ===================== 核心任务执行 =====================
def execute_task(user_task: str, is_iter: bool, need_save: bool) -> bool:
    global total_cost_all, total_input_tokens, total_output_tokens
    global session_history, current_task_id, current_task_simple_name, current_task_type

    start_all = time.time()
    task_in_tok = 0
    task_out_tok = 0

    # 全新任务且需要保存：分配编号、精简文件名
    if not is_iter and need_save:
        init_dirs()
        current_task_id += 1
        save_task_counter(current_task_id)
        current_task_simple_name = clean_filename(user_task)

    task_simple_name = current_task_simple_name
    task_id = current_task_id

    # 拼接上下文
    prompt_with_history = "\n".join(session_history) + "\n当前新指令：" + user_task

    try:
        # 1. 需求解析
        req_res = api_retry(lambda: user_proxy.initiate_chat(requirement_agent, message=prompt_with_history, silent=True))
        if hasattr(req_res, 'usage') and req_res.usage:
            in1 = req_res.usage.get("prompt_tokens", estimate_token_count(prompt_with_history))
            out1 = req_res.usage.get("completion_tokens", estimate_token_count(req_res.chat_history[-1]["content"]))
        else:
            in1 = estimate_token_count(prompt_with_history)
            out1 = estimate_token_count(req_res.chat_history[-1]["content"])
        task_in_tok += in1
        task_out_tok += out1

        req_full = req_res.chat_history[-1]["content"]
        if not is_iter:
            current_task_type = get_task_type(req_full)
        task_type = current_task_type

        # 2. 生成主体内容
        content1_res = api_retry(lambda: user_proxy.initiate_chat(content_agent, message=req_full, silent=True))
        if hasattr(content1_res, 'usage') and content1_res.usage:
            in2 = content1_res.usage.get("prompt_tokens", estimate_token_count(req_full))
            out2 = content1_res.usage.get("completion_tokens", estimate_token_count(content1_res.chat_history[-1]["content"]))
        else:
            in2 = estimate_token_count(req_full)
            out2 = estimate_token_count(content1_res.chat_history[-1]["content"])
        task_in_tok += in2
        task_out_tok += out2

        content1 = content1_res.chat_history[-1]["content"]
        if task_type == "code":
            content1 = extract_python_code(content1)

        # 3. 优化/测试
        content2_res = api_retry(lambda: user_proxy.initiate_chat(review_agent, message=f"{req_full}\n{content1}", silent=True))
        if hasattr(content2_res, 'usage') and content2_res.usage:
            in3 = content2_res.usage.get("prompt_tokens", estimate_token_count(req_full+content1))
            out3 = content2_res.usage.get("completion_tokens", estimate_token_count(content2_res.chat_history[-1]["content"]))
        else:
            in3 = estimate_token_count(req_full+content1)
            out3 = estimate_token_count(content2_res.chat_history[-1]["content"])
        task_in_tok += in3
        task_out_tok += out3

        content2 = content2_res.chat_history[-1]["content"]
        if task_type == "code":
            content2 = extract_python_code(content2)

        # 保存上下文
        session_history.append(f"历史指令：{user_task}")
        session_history.append(f"历史结果摘要：{content1[:200]}")

        # ========== 默认只控制台输出 ==========
        print(f"\n{CYAN}==================== 生成结果 ===================={RESET}")
        if task_type == "code":
            print(f"{BLUE}【业务代码】{RESET}\n")
            print(content1)
            print(f"\n{BLUE}【测试用例】{RESET}\n")
            print(content2)
        else:
            print(f"{BLUE}【正文内容】{RESET}\n")
            print(content1)
            print(f"\n{BLUE}【优化润色版】{RESET}\n")
            print(content2)
        print(f"{CYAN}================================================{RESET}")

        # ========== 仅用户要求时才保存文件 ==========
        if need_save:
            task_cost = calc_cost(task_in_tok, task_out_tok)
            total_input_tokens += task_in_tok
            total_output_tokens += out_tok
            total_cost_all = calc_cost(total_input_tokens, total_output_tokens)
            total_time = time.time() - start_all

            file_paths = []
            if task_type == "code":
                p1 = os.path.join(CODE_DIR, f"task_{task_id}_{task_simple_name}_func.py")
                p2 = os.path.join(CODE_DIR, f"task_{task_id}_{task_simple_name}_test.py")
                save_file(p1, content1)
                save_file(p2, content2)
                file_paths.extend([p1,p2])
            else:
                p1 = os.path.join(DOC_DIR, f"task_{task_id}_{task_simple_name}_正文.md")
                p2 = os.path.join(DOC_DIR, f"task_{task_id}_{task_simple_name}_优化版.md")
                save_file(p1, content1)
                save_file(p2, content2)
                file_paths.extend([p1,p2])

            # 日志+报告
            report_path = generate_markdown_report(task_id, task_simple_name, user_task, req_full,
                                                   content1, content2, task_type,
                                                   task_in_tok, task_out_tok, task_cost, total_cost_all, total_time)
            save_task_log(task_id, task_simple_name, user_task, "SUCCESS", "已生成并保存文件",
                          task_in_tok, task_out_tok, task_cost, total_time, task_type)

            print(f"\n{GREEN}📂 已按要求生成并保存文件：{RESET}")
            for path in file_paths:
                print(f"   {path}")
            print(f"   任务报告：{report_path}")

        return True

    except Exception as e:
        print(f"\n{RED}❌ 任务失败：{str(e)}{RESET}")
        return False

# ===================== 主程序 =====================
def main():
    global current_task_id, session_history
    current_task_id = load_task_counter()

    print(f"{CYAN}🏆 智能问答系统【默认仅控制台输出｜按需生成文件】{RESET}")
    print(f"{CYAN}{'='*75}{RESET}")
    print(f"📊 历史存档任务编号：task_{current_task_id}")
    print("💡 普通提问：直接输入，仅控制台展示结果，不生成任何文件")
    print("💡 需要存档：指令加「保存/导出/生成文件」，自动归档")
    print("💡 new 清空上下文 | exit 退出 | help 帮助\n")

    while True:
        user_input = input(">>> 请输入需求：").strip()

        if user_input.lower() in ["exit", "quit", "q"]:
            print(f"\n{CYAN}📊 程序退出账单汇总{RESET}")
            print(f"   累计输入Token：{total_input_tokens}")
            print(f"   累计输出Token：{total_output_tokens}")
            print(f"   全程总花费：{total_cost_all:.6f} 元")
            print("\n👋 已安全退出！")
            break

        if user_input.lower() == "new":
            session_history.clear()
            print(f"{GREEN}✅ 已清空会话上下文，开启全新对话{RESET}")
            continue

        if user_input.lower() == "help":
            print(f"\n{BLUE}📖 使用说明{RESET}")
            print("  1. 默认模式：所有结果只打印在控制台，不自动建文件")
            print("  2. 需保存归档：指令带上 保存/导出/生成文件 即可")
            print("  3. 支持多轮迭代上下文，可直接优化上一轮内容")
            print("  4. 仅存档时占用task累计编号、生成日志和报告")
            print("  5. new 清上下文，exit 退出\n")
            continue

        if not user_input:
            print(f"{RED}❌ 需求不能为空！{RESET}")
            continue

        # 检测是否需要保存文件
        need_save = check_need_save(user_input)
        # 是否迭代会话
        is_iter = len(session_history) > 0

        execute_task(user_input, is_iter, need_save)
        print(f"\n{'-'*75}")

if __name__ == "__main__":
    main()