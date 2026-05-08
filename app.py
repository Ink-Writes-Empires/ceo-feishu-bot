"""
跨境电商AI系统 - 飞书双向通信服务端
部署到 Railway / Render / 任何云服务即可

功能：
1. 接收飞书群聊@消息
2. 转发到WorkBuddy/AI处理
3. 回复结果到飞书群
"""

import json
import os
import requests
import time
import threading
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)

# ==================== 配置 ====================
FEISHU_APP_ID = "cli_a9776a466eb89bdd"
FEISHU_APP_SECRET = "ue1r5dYTqWVbaxoM3VnYEcU8EUnX68hC"

# 消息转发目标 - CEO处理后的响应会写到这里
RESPONSE_FILE = "responses.json"
MESSAGE_QUEUE = []  # 待处理的消息队列

# ==================== 飞书API ====================
class FeishuClient:
    """飞书API客户端"""
    
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = None
        self.token_expires_at = 0
    
    def _get_token(self):
        """获取tenant_access_token"""
        if time.time() < self.token_expires_at:
            return self.token
        
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = {"app_id": self.app_id, "app_secret": self.app_secret}
        
        try:
            resp = requests.post(url, json=data, timeout=10)
            result = resp.json()
            if result.get("code") == 0:
                self.token = result["tenant_access_token"]
                self.token_expires_at = time.time() + result.get("expire", 7200) - 300
                return self.token
        except Exception as e:
            print(f"[ERROR] 获取token失败: {e}")
        return None
    
    def send_message(self, chat_id, text, msg_type="text"):
        """发送消息到飞书"""
        token = self._get_token()
        if not token:
            return False
        
        url = "https://open.feishu.cn/open-apis/im/v1/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        if msg_type == "text":
            content = json.dumps({"text": text})
        elif msg_type == "post":
            content = json.dumps({
                "zh_cn": {
                    "title": "CEO决策简报",
                    "content": [[{"tag": "text", "text": text}]]
                }
            })
        elif msg_type == "interactive":
            content = text  # 已经是JSON字符串
        
        data = {
            "receive_id": chat_id,
            "msg_type": msg_type,
            "content": content if isinstance(content, str) else json.dumps(content)
        }
        
        try:
            resp = requests.post(url, headers=headers, json=data, timeout=10)
            result = resp.json()
            if result.get("code") != 0:
                print(f"[ERROR] 发送消息失败: {result}")
            return result.get("code") == 0
        except Exception as e:
            print(f"[ERROR] 发送消息异常: {e}")
            return False
    
    def get_user_info(self, user_id):
        """获取用户信息"""
        token = self._get_token()
        if not token:
            return None
        
        url = f"https://open.feishu.cn/open-apis/contact/v3/users/{user_id}"
        headers = {"Authorization": f"Bearer {token}"}
        
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            return resp.json()
        except:
            return None


feishu = FeishuClient(FEISHU_APP_ID, FEISHU_APP_SECRET)


# ==================== 消息处理 ====================

def process_message(message_data):
    """处理收到的消息"""
    try:
        event = message_data.get("event", {})
        sender = event.get("sender", {})
        message = event.get("message", {})
        
        chat_id = message.get("chat_id", "")
        chat_type = message.get("chat_type", "")  # "group" or "p2p"
        msg_id = message.get("message_id", "")
        
        # 解析消息内容
        content_str = message.get("content", "{}")
        content = json.loads(content_str) if isinstance(content_str, str) else content_str
        text = content.get("text", "").strip()
        
        # 获取发送者信息
        sender_id = sender.get("sender_id", {}).get("user_id", "")
        
        print(f"\n[RECEIVED] {datetime.now().strftime('%H:%M:%S')}")
        print(f"  来自: {chat_type} - {chat_id}")
        print(f"  消息: {text[:100]}")
        
        # 过滤掉空消息和系统消息
        if not text:
            return
        
        # 发送"正在处理"提示
        feishu.send_message(chat_id, "🤖 CEO收到，正在处理中...")
        
        # 将消息加入队列，等待CEO处理
        MESSAGE_QUEUE.append({
            "chat_id": chat_id,
            "msg_id": msg_id,
            "sender_id": sender_id,
            "text": text,
            "received_at": datetime.now().isoformat(),
            "processed": False
        })
        
    except Exception as e:
        print(f"[ERROR] 处理消息失败: {e}")


def send_ceo_response(chat_id, response_text):
    """发送CEO的回复到飞书"""
    # 如果回复太长，分段发送
    if len(response_text) > 1500:
        parts = [response_text[i:i+1500] for i in range(0, len(response_text), 1500)]
        for i, part in enumerate(parts):
            header = f"📋 CEO回复 (第{i+1}/{len(parts)}部分)\n" if len(parts) > 1 else ""
            feishu.send_message(chat_id, f"{header}{part}")
            time.sleep(0.5)
    else:
        feishu.send_message(chat_id, f"🤖 CEO回复：\n{response_text}")


# ==================== Webhook端点 ====================

@app.route("/", methods=["GET"])
def index():
    """健康检查"""
    return jsonify({
        "status": "running",
        "service": "跨境电商AI系统 - 飞书双向通信",
        "version": "1.0",
        "queue_size": len(MESSAGE_QUEUE),
        "uptime": time.time() - start_time
    })


@app.route("/webhook/feishu", methods=["POST"])
def feishu_webhook():
    """接收飞书事件推送"""
    data = request.json
    
    # URL验证（首次配置时需要）
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge", "")})
    
    # 处理消息事件
    header = data.get("header", {})
    event_type = header.get("event_type", "")
    
    if event_type in ["im.message.receive_v1", "im.message.group_v1"]:
        # 异步处理消息
        thread = threading.Thread(target=process_message, args=(data,))
        thread.start()
    
    return jsonify({"code": 0, "msg": "success"})


@app.route("/api/messages/pending", methods=["GET"])
def get_pending_messages():
    """CEO轮询获取待处理消息"""
    pending = [m for m in MESSAGE_QUEUE if not m["processed"]]
    return jsonify({
        "count": len(pending),
        "messages": pending
    })


@app.route("/api/messages/respond", methods=["POST"])
def respond_to_message():
    """CEO回复消息"""
    data = request.json
    chat_id = data.get("chat_id")
    response = data.get("response")
    
    if not chat_id or not response:
        return jsonify({"error": "缺少参数"}), 400
    
    send_ceo_response(chat_id, response)
    
    # 标记消息已处理
    for msg in MESSAGE_QUEUE:
        if msg["chat_id"] == chat_id and not msg["processed"]:
            msg["processed"] = True
            msg["response"] = response
            msg["responded_at"] = datetime.now().isoformat()
    
    return jsonify({"status": "sent"}), 200


@app.route("/api/messages/history", methods=["GET"])
def get_message_history():
    """查看消息历史"""
    limit = request.args.get("limit", 20, type=int)
    return jsonify({
        "total": len(MESSAGE_QUEUE),
        "messages": MESSAGE_QUEUE[-limit:]
    })


@app.route("/api/status", methods=["GET"])
def check_status():
    """检查系统状态"""
    token = feishu._get_token()
    return jsonify({
        "feishu_token": "valid" if token else "invalid",
        "message_queue": len(MESSAGE_QUEUE),
        "processed": sum(1 for m in MESSAGE_QUEUE if m["processed"]),
        "pending": sum(1 for m in MESSAGE_QUEUE if not m["processed"])
    })


# ==================== 启动 ====================
start_time = time.time()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"""
╔══════════════════════════════════════════╗
║   跨境电商AI系统 - 飞书服务端            ║
║   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}            ║
║   App ID: {FEISHU_APP_ID}  ║
║   Port: {port}                             ║
╚══════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=port)
