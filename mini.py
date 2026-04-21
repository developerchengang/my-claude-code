"""极简版 Claude Code —— ReAct 循环 + 工具调用，就这些。

读这个文件的顺序：
    1. 工具实现（read_file / run_bash）
    2. TOOL_SCHEMAS：告诉大模型有哪些工具可以用
    3. agent_turn：内循环，反复调工具直到模型说"我够了"
    4. main：外循环，普通 REPL
"""

import json
import os
import subprocess

from openai import OpenAI

# 改成你自己的 key；默认配了 DeepSeek，换 OpenAI / 通义 / Ollama 都行
client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY", "sk-xxx"),
    base_url=os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com"),
)
MODEL = os.environ.get("OPENAI_MODEL", "deepseek-chat")

SYSTEM_PROMPT = """你是一个运行在用户终端里的编程助手。
需要看文件或跑命令时，就调用对应的工具。
一次不够就多调几次，直到有足够信息回答用户。"""


# ---------- 工具 ----------

def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def run_bash(command: str) -> str:
    r = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=30
    )
    return f"[exit={r.returncode}]\n{r.stdout}{r.stderr}"


TOOLS = {"read_file": read_file, "run_bash": run_bash}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个本地文件的全部内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "文件路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "在本机执行一条 shell 命令，返回 stdout+stderr",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string", "description": "要执行的命令"}},
                "required": ["command"],
            },
        },
    },
]


# ---------- ReAct 循环 ----------

def agent_turn(messages: list) -> str:
    """一次用户输入 = 一次 agent turn，内部可能跑多轮 tool call。"""
    while True:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        # 模型没要求调工具，说明它觉得可以直接回答了
        if not msg.tool_calls:
            return msg.content or ""

        # 模型要求调工具：一个个执行，结果喂回去，继续下一轮
        for call in msg.tool_calls:
            args = json.loads(call.function.arguments or "{}")
            print(f"  [tool] {call.function.name}({args})")
            # 工具报错不终止 loop，错误信息也是给模型的反馈
            try:
                result = str(TOOLS[call.function.name](**args))
            except Exception as e:
                result = f"[error] {type(e).__name__}: {e}"
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })


# ---------- REPL ----------

def main():
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("mini-claude-code: Ctrl+C 退出\n")
    while True:
        try:
            user = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        print(agent_turn(messages), "\n")


if __name__ == "__main__":
    main()
