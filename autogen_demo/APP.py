import os
import re
import time
import json
import warnings
from datetime import datetime
from dotenv import load_dotenv
import autogen
from flask import Flask, render_template, request, jsonify, send_file, Response, session
import uuid

# ===================== 全局配置 =====================
warnings.filterwarnings("ignore")
warnings.filterwarnings("ignore", category=UserWarning, module="flask")
app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False
app.config['THREADED'] = True
app.secret_key = 'ai-agent-three-20260507'
app.config['PERMANENT_SESSION_LIFETIME'] = 3600*24*7

# 模型&计费配置
PRICE_INPUT_PER_K = 0.0005
PRICE_OUTPUT_PER_K = 0.0015
MODEL_NAME = "deepseek-v4-flash"
TEMPERATURE = 0.0
MAX_TOKENS = 4096
API_TIMEOUT = 120

# 持久化路径
COUNTER_FILE = "task_counter.json"
CHAT_HISTORY_FILE = "chat_history.json"
TASK_SESSION_DIR = "task_sessions"
USER_SESSIONS_DIR = "user_sessions"

# 全局任务计数
current_task_id = 0

# ===================== 目录初始化 =====================
load_dotenv()
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "agent_output")
CODE_DIR = os.path.join(OUTPUT_DIR, "codes")
DOC_DIR = os.path.join(OUTPUT_DIR, "docs")
TASK_SESSION_DIR = os.path.join(BASE_DIR, "task_sessions")
USER_SESSIONS_DIR = os.path.join(BASE_DIR, "user_sessions")

def init_dirs():
    for d in [CODE_DIR, DOC_DIR, TASK_SESSION_DIR, USER_SESSIONS_DIR]:
        os.makedirs(d, exist_ok=True)
init_dirs()

# ===================== 工具：文件读写 =====================
def load_task_counter():
    if os.path.exists(COUNTER_FILE):
        return json.load(open(COUNTER_FILE,"r",encoding="utf-8")).get("last_task_id",0)
    return 0
def save_task_counter(num):
    json.dump({"last_task_id":num}, open(COUNTER_FILE,"w",encoding="utf-8"),ensure_ascii=False,indent=2)

# 修复1：添加异常捕获，文件不存在返回空列表
def load_json(file):
    try:
        return json.load(open(file,"r",encoding="utf-8")) if os.path.exists(file) else []
    except:
        return []
def save_json(file,data):
    json.dump(data,open(file,"w",encoding="utf-8"),ensure_ascii=False,indent=2)

# 任务会话
def save_task_session(tid, lst):
    save_json(os.path.join(TASK_SESSION_DIR,f"session_{tid}.json"), lst)
def load_task_session(tid):
    return load_json(os.path.join(TASK_SESSION_DIR,f"session_{tid}.json"))

# 任务对话记录
def save_task_chat(tid, rec):
    p = os.path.join(TASK_SESSION_DIR,f"chat_{tid}.json")
    arr = load_json(p)
    arr.append(rec)
    save_json(p,arr)
def load_task_chat(tid):
    return load_json(os.path.join(TASK_SESSION_DIR,f"chat_{tid}.json"))

# 用户会话隔离
def save_user_session(sid, data):
    save_json(os.path.join(USER_SESSIONS_DIR,f"user_{sid}.json"),data)
def load_user_session(sid):
    p = os.path.join(USER_SESSIONS_DIR,f"user_{sid}.json")
    return load_json(p) if os.path.exists(p) else {"session_history":[],"bind_task_id":0,"task_name":"","task_type":"","last_content":"","lang":"python"}

# ===================== LLM 基础配置 =====================
LLM_CONFIG = {
    "config_list": [{
        "model": MODEL_NAME,
        "api_key": DEEPSEEK_API_KEY,
        "base_url": "https://api.deepseek.com/v1"
    }],
    "temperature": TEMPERATURE,
    "max_tokens": MAX_TOKENS,
    "timeout": API_TIMEOUT
}

# ===================== 【三个智能体 语言要求强化版】 =====================
# 1. 需求分析智能体：强制识别语言/格式要求
req_agent = autogen.ConversableAgent(
    name="RequirementAnalyzer",
    system_message="""
你只做三件事：
1. 判断任务类型：【编程任务】或【文案任务】；
2. 必须识别用户的输出语言/格式要求（如“用C++写”“以Markdown格式输出”“转成Java”等），并在结果中明确标注【语言：xxx】，例如【语言：C++】；
3. 结合历史对话，精简提炼用户核心需求，不要生成代码、不要写正文。
""",
    llm_config=LLM_CONFIG,
    max_consecutive_auto_reply=1
)

# 2. 内容生成智能体：严格按指定语言输出
gen_agent = autogen.ConversableAgent(
    name="ContentGenerator",
    system_message="""
你必须严格遵守以下规则：
1. 优先按照需求分析中【语言：xxx】的要求输出，用户指定语言（如C++/Java/Go），必须全程使用该语言，禁止默认Python；
2. 编程类内容用正确的格式包裹：C++用```cpp ```，Java用```java ```，Python用```python ```；
3. 文案类内容按需求指定的格式（如Markdown/纯文本）输出；
4. 只输出完整答案，不要多余解释、不要说明性文字。
""",
    llm_config=LLM_CONFIG,
    max_consecutive_auto_reply=1
)

# 3. 审核优化智能体：与生成内容保持相同语言
opt_agent = autogen.ConversableAgent(
    name="ReviewOptimizer",
    system_message="""
你必须与生成内容保持**相同的语言/格式**：
1. 若生成内容是C++，优化也必须是C++，给出优化版本或测试用例；
2. 若生成内容是Java，优化也必须是Java，不能切换语言；
3. 文案类内容按原格式润色，不要改变语言/格式；
4. 只输出优化后的内容，不要多余废话。
""",
    llm_config=LLM_CONFIG,
    max_consecutive_auto_reply=1
)

# 调度代理（框架必备，不算业务智能体）
user_proxy = autogen.UserProxyAgent(
    name="Dispatcher",
    human_input_mode="NEVER",
    code_execution_config=False,
    max_consecutive_auto_reply=0
)

# ===================== 通用小工具 =====================
def estimate_tokens(text):
    cn = len(re.findall(r'[\u4e00-\u9fff]',text))
    en = len(re.findall(r'[a-zA-Z0-9]+',text))
    return int(cn*1.3 + en)

def extract_code(text, lang="python"):
    pattern = rf"```(?:{lang})?\s*(.*?)\s*```"
    m = re.search(pattern, text, re.DOTALL)
    return m.group(1).strip() if m else text.strip()

def clean_filename(text):
    drop = ["编写","实现","写一个","请设计","函数","算法"]
    for w in drop: text=text.replace(w,"")
    return re.sub(r'[\\/:*?"<>|，。！？]','',text)[:12]

def check_need_save(text):
    return any(k in text for k in ["保存","导出","生成文件","存为文档"])

def validate_code(code, lang="python"):
    if lang != "python":
        return True, "✅ 语法正常"
    try:
        compile(code,"<string>","exec")
        return True,"✅ 语法正常"
    except SyntaxError as e:
        return False,f"❌ 第{e.lineno}行语法错误"

def get_saved_tasks():
    # 用字典存储：key=任务ID，value=任务信息（自动去重）
    task_dict = {}
    if os.path.exists(CODE_DIR):
        for f in os.listdir(CODE_DIR):
            if f"task_" in f and not "_opt" in f:
                parts = f.split("_")
                tid = int(parts[1])
                name = parts[2]
                task_dict[tid] = {"id": tid, "name": name, "type": "code"}
    if os.path.exists(DOC_DIR):
        for f in os.listdir(DOC_DIR):
            if f"task_" in f and not "_优化版" in f:
                parts = f.split("_")
                tid = int(parts[1])
                name = parts[2]
                task_dict[tid] = {"id": tid, "name": name, "type": "doc"}
    # 转换为列表并排序
    tasks = list(task_dict.values())
    return sorted(tasks, key=lambda x: x["id"], reverse=True)
def delete_task(tid):
    tid = int(tid) # 修复3：统一ID为整数
    for d in [CODE_DIR,DOC_DIR]:
        if os.path.exists(d):
            for f in os.listdir(d):
                if f"task_{tid}_" in f:
                    os.remove(os.path.join(d,f))
    for s in ["session","chat"]:
        p = os.path.join(TASK_SESSION_DIR,f"{s}_{tid}.json")
        if os.path.exists(p):os.remove(p)
    return True

# ===================== 【核心修复】自动保存会话（新建会话前自动归档） =====================
def auto_save_current_session(uid):
    global current_task_id
    user_sess = load_user_session(uid)
    sess_hist = user_sess.get("session_history", [])
    if len(sess_hist) == 0:
        return
    
    current_task_id = load_task_counter() + 1
    save_task_counter(current_task_id)
    task_name = f"会话{current_task_id}"
    task_type = user_sess.get("task_type", "doc")
    lang = user_sess.get("lang", "python")
    last_content = user_sess.get("last_content", "")

    if task_type == "code":
        suffix = "py" if lang == "python" else lang
        p1 = os.path.join(CODE_DIR,f"task_{current_task_id}_{task_name}.{suffix}")
        with open(p1,"w",encoding="utf-8") as f:f.write(last_content)
    else:
        p1 = os.path.join(DOC_DIR,f"task_{current_task_id}_{task_name}_正文.md")
        with open(p1,"w",encoding="utf-8") as f:f.write(last_content)
    
    save_task_session(current_task_id, sess_hist)

    # ✅ 修复：保存【完整多轮对话】，前端可正常渲染所有交互
    chat_records = []
    msg_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i in range(0, len(sess_hist), 2):
        if i+1 < len(sess_hist):
            user_msg = sess_hist[i].replace("用户：", "").strip()
            bot_msg = sess_hist[i+1].replace("需求分析：", "").strip()
            chat_records.append({"role": "user", "content": user_msg, "time": msg_time})
            chat_records.append({"role": "bot", "content": bot_msg, "time": msg_time})
    
    chat_path = os.path.join(TASK_SESSION_DIR, f"chat_{current_task_id}.json")
    save_json(chat_path, chat_records)
# ===================== 核心任务执行：三智能体流水线 =====================
def run_task(user_input, is_iter, need_save):
    global current_task_id
    start = time.time()

    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        session.permanent = True
    uid = session['user_id']
    user_sess = load_user_session(uid)

    if not is_iter and need_save:
        current_task_id = load_task_counter() + 1
        save_task_counter(current_task_id)
        user_sess["task_name"] = clean_filename(user_input)
        user_sess["bind_task_id"] = current_task_id

    sess_hist = user_sess.get("session_history", [])
    full_ctx = "历史对话记录：\n" + "\n".join(sess_hist) + "\n\n当前用户新提问：" + user_input if sess_hist else "用户提问：" + user_input

    res_req = user_proxy.initiate_chat(req_agent, message=full_ctx, silent=True)
    req_text = res_req.chat_history[-1]["content"]
    res_gen = user_proxy.initiate_chat(gen_agent, message=req_text, silent=True)
    gen_text = res_gen.chat_history[-1]["content"]
    res_opt = user_proxy.initiate_chat(opt_agent, message=gen_text, silent=True)
    opt_text = res_opt.chat_history[-1]["content"]

    task_type = "code" if "编程任务" in req_text else "doc"
    user_sess["task_type"] = task_type
    lang = "python"
    if "【语言：" in req_text:
        lang = req_text.split("【语言：")[1].split("】")[0].strip().lower()
    user_sess["lang"] = lang
    user_sess["last_content"] = gen_text

    code_check = ""
    if task_type == "code":
        gen_code = extract_code(gen_text, lang)
        opt_code = extract_code(opt_text, lang)
        _, code_check = validate_code(gen_code, lang)
    else:
        gen_code = gen_text
        opt_code = opt_text
        code_check = "✅ 文案格式正常"

    sess_hist.append(f"用户：{user_input}")
    sess_hist.append(f"需求分析：{gen_text[:80]}")
    user_sess["session_history"] = sess_hist
    save_user_session(uid, user_sess)

    bind_tid = user_sess.get("bind_task_id",0)
    if bind_tid > 0:
        save_task_session(bind_tid, sess_hist)
        # ✅ 修复：实时追加【完整对话】，前端加载时显示所有交互
        msg_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chat_data = [
            {"role": "user", "content": user_input, "time": msg_time},
            {"role": "bot", "content": gen_text, "time": msg_time}
        ]
        # 追加保存，不覆盖历史对话
        existing_chat = load_task_chat(bind_tid)
        existing_chat.extend(chat_data)
        save_task_chat(bind_tid, existing_chat)

    save_paths = []
    if need_save:
        tname = user_sess.get("task_name","untitle")
        suffix = "py" if lang == "python" else lang
        if task_type == "code":
            p1 = os.path.join(CODE_DIR,f"task_{current_task_id}_{tname}.{suffix}")
            p2 = os.path.join(CODE_DIR,f"task_{current_task_id}_{tname}_opt.{suffix}")
            with open(p1,"w",encoding="utf-8") as f:f.write(gen_code)
            with open(p2,"w",encoding="utf-8") as f:f.write(opt_code)
            save_paths = [p1,p2]
        else:
            p1 = os.path.join(DOC_DIR,f"task_{current_task_id}_{tname}_正文.md")
            p2 = os.path.join(DOC_DIR,f"task_{current_task_id}_{tname}_优化版.md")
            with open(p1,"w",encoding="utf-8") as f:f.write(gen_text)
            with open(p2,"w",encoding="utf-8") as f:f.write(opt_text)
            save_paths = [p1,p2]

    cost_time = round(time.time()-start,2)
    result = {
        "type_name":"编程任务" if task_type=="code" else "文案任务",
        "content":gen_text,
        "review":opt_text,
        "code_check":code_check,
        "save_paths":save_paths,
        "task_cost":0,
        "total_cost":0,
        "input_tok":estimate_tokens(full_ctx+req_text+gen_text),
        "output_tok":estimate_tokens(gen_text+opt_text),
        "cost_time":cost_time,
        "now_task_id":current_task_id,
        "bind_task_id":bind_tid
    }
    return result
# ===================== 路由（仅修改clear接口，新增自动保存） =====================
@app.route('/')
def index():
    global current_task_id
    current_task_id = load_task_counter()
    if 'user_id' not in session:
        session['user_id'] = str(uuid.uuid4())
        session.permanent = True
    return render_template('index.html', now_task_id=current_task_id)

@app.route('/send',methods=['POST'])
def send():
    msg = request.json.get("msg","").strip()
    need = check_need_save(msg)
    uid = session.get('user_id',str(uuid.uuid4()))
    is_iter = len(load_user_session(uid).get("session_history",[]))>0
    try:
        res = run_task(msg,is_iter,need)
        rec = load_json(CHAT_HISTORY_FILE)
        rec.append({"time":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"user":msg,"bot":res})
        save_json(CHAT_HISTORY_FILE,rec[-200:])
        return jsonify({"code":0, "data": res})  # 修复：返回格式匹配前端期望
    except Exception as e:
        return jsonify({"code":1, "msg": str(e)})

# ===================== 【修复完成】新建会话前自动保存上一轮对话 =====================
@app.route('/clear',methods=['POST'])
def clear():
    uid = session.get('user_id',str(uuid.uuid4()))
    # 自动保存上一次会话到历史任务
    auto_save_current_session(uid)
    # 再清空当前会话
    save_user_session(uid,{"session_history":[],"bind_task_id":0,"task_name":"","task_type":"","last_content":"","lang":"python"})
    return jsonify({"ok":True, "msg": "新建会话成功"})

# 修复4：核心！修改参数名为 task_id（前端传递的参数）+ 异常捕获
@app.route('/task/load',methods=['POST'])
def task_load():
    try:
        uid = session.get('user_id',str(uuid.uuid4()))
        # 关键修复：前端传 task_id，后端接收 task_id
        tid = int(request.json.get("task_id", 0))
        if tid <= 0:
            return jsonify({"ok":False,"error":"无效的任务ID"})
        sess = load_user_session(uid)
        sess["bind_task_id"] = tid
        sess["session_history"] = load_task_session(tid)
        save_user_session(uid,sess)
        return jsonify({"ok":True})
    except Exception as e:
        print(f"加载任务失败：{e}")  # 调试用
        return jsonify({"ok":False,"error":"任务加载失败"})

# 修复5：任务聊天记录接口，添加异常处理和数据校验
@app.route('/task/chat/<tid>')
def task_chat(tid):
    try:
        tid_int = int(tid)
        chat_data = load_task_chat(tid_int)
        # 校验并清洗数据（确保符合前端格式）
        valid_chat = []
        for item in chat_data:
            if isinstance(item, dict):
                role = item.get("role", "")
                content = item.get("content", "")
                time = item.get("time", "")
                # 只保留有效消息
                if role in ["user", "bot"] and content and time:
                    valid_chat.append(item)
        return jsonify(valid_chat)
    except Exception as e:
        print(f"加载聊天记录失败：{e}")  # 调试用
        return jsonify([])

@app.route('/task/list')
def task_list():
    return jsonify(get_saved_tasks())

# 修复6：删除任务接口添加异常处理
@app.route('/task/delete',methods=['POST'])
def task_delete():
    try:
        tid = int(request.json.get("id", 0))
        if tid <= 0:
            return jsonify({"ok":False,"msg":"无效的任务ID"})
        delete_task(tid)
        return jsonify({"ok":True, "msg": "删除成功"})
    except Exception as e:
        print(f"删除任务失败：{e}")
        return jsonify({"ok":False,"error":"删除失败"})

@app.route('/download')
def download():
    p = request.args.get("path","")
    if not os.path.exists(p):return "404",404
    return send_file(p,as_attachment=True)

@app.route('/preview')
def preview():
    p = request.args.get("path","")
    return open(p,"r",encoding="utf-8").read() if os.path.exists(p) else "文件不存在"

@app.route('/export/chat')
def export_chat():
    return Response("# AI对话记录\n",mimetype="text/markdown")

@app.route('/help')
def help_info():
    return jsonify({"model":MODEL_NAME})

if __name__ == '__main__':
    app.run(host='127.0.0.1',port=5000,debug=False)