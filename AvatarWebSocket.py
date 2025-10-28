# -*- coding: utf-8 -*-
import time
import uuid
import queue
import json
from ws4py.client.threadedclient import WebSocketClient
from ws4py.client.threadedclient import WebSocketBaseClient
import _thread
import AipaasAuth
import threading

# 引入 PySide6 相关模块
from PySide6.QtCore import QObject, Signal, Slot


class StreamUrlSignaler(QObject):
    """
    用于在非 GUI 线程中接收数据并在 GUI 线程中发送信号的 QObject。
    """
    stream_url_signal = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)


class avatarWebsocket(WebSocketClient, threading.Thread):

    def __init__(self, url, protocols=None, extensions=None, heartbeat_freq=None, ssl_options=None, headers=None,
                 exclude_headers=None, signaler=None):
        WebSocketBaseClient.__init__(self, url, protocols=None, extensions=None, heartbeat_freq=None, ssl_options=None,
                                     headers=None, exclude_headers=None)
        threading.Thread.__init__(self)
        self._th = threading.Thread(target=super().run, name='WebSocketClient')
        self._th.daemon = True
        self.appId = ''
        self.vcn = ''
        self.anchorId = ''
        self.dataList = queue.Queue(maxsize=100)
        self.status = True
        self.linkConnected = False
        self.avatarLinked = False
        # 添加 signaler 用于 PySide6 信号通信
        self.signaler = signaler

    def run(self):
        try:
            print("[WebSocket] 正在连接服务器...")
            self.connect()
            # 移除立即调用 self.connectAvatar()，等待 opened()
            _thread.start_new_thread(self.send_Message, ())
            while self.status and not self.terminated:
                self._th.join(timeout=0.1)
        except Exception as e:
            self.status = False
            print(f"[ERROR] WebSocket 异常退出: {e}")

    def stop(self):
        self.status = False
        self.close(code=1000)

    def send_Message(self):
        """
        send msg to server, if no message to send, send ping msg
        :return:
        """
        while self.status:
            if self.linkConnected:
                try:
                    if self.avatarLinked:
                        task = self.dataList.get(block=True, timeout=5)
                        print('%s send msg: %s' % (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(time.time())), task))
                        self.send(task)
                except queue.Empty:
                    if self.status and self.avatarLinked:
                        self.send(self.getPingMsg())
                    else:
                        time.sleep(0.1)
                except AttributeError:
                    pass
            else:
                time.sleep(0.1)

    def sendDriverText(self, driverText):
        """
        send text msg, interactive_mode default 0
        :param driverText:
        :return:
        """
        try:
            textMsg = {
                "header": {
                    "app_id": self.appId,
                    "request_id": str(uuid.uuid4()),
                    "ctrl": "text_driver"
                },
                "parameter": {
                    "tts": {
                        "vcn": self.vcn
                    },
                    "avatar_dispatch": {
                        "interactive_mode": 0
                    }
                },
                "payload": {
                    "text": {
                        "content": driverText
                    }
                }
            }
            self.dataList.put_nowait(json.dumps(textMsg))
        except Exception as e:
            print(e)

    def connectAvatar(self):
        """
        连接成功后，发送启动请求
        """
        # 统一使用 1280x720 分辨率，匹配 PySide6 窗口大小
        video_width = 1280
        video_height = 720

        start_request = {
            "header": {
                "app_id": self.appId,
                "request_id": str(uuid.uuid4()),
                "ctrl": "start"
            },
            "parameter": {
                "tts": {
                    "vcn": self.vcn
                },
                "avatar": {
                    "stream": {
                        "protocol": "xrtc",
                        "fps": 25,
                        "bitrate": 2000
                    },
                    "avatar_id": self.anchorId,
                    "width": video_width,
                    "height": video_height
                }
            }
        }

        # 打印并发送请求 (保持不变)
        print('%s send start request: %s' % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                                             json.dumps(start_request)))
        self.send(json.dumps(start_request))

    def getPingMsg(self):
        """
        :return: ping msg
        """
        pingMsg = {
            "header": {
                "app_id": self.appId,
                "request_id": str(uuid.uuid4()),
                "ctrl": "ping"
            },
        }
        return json.dumps(pingMsg)

    def opened(self):
        """
        ws connected, msg can be sent
        :return:
        """
        print('[WebSocket] 连接成功，开始发送启动消息...')
        self.linkConnected = True
        # 【关键修正】: 在连接建立后发送启动请求
        self.connectAvatar()

    def closed(self, code, reason=None):
        msg = 'receive closed, code: ' + str(code)
        print(msg)
        self.status = False

    def received_message(self, message):
        try:
            data = json.loads(str(message))
            if data['header']['code'] != 0:
                self.status = False
                print('receive error msg: %s' % str(message))
            else:
                if 'avatar' in data['payload'] and data['payload']['avatar']['error_code'] == 0 and \
                        data['payload']['avatar']['event_type'] == 'stop':
                    raise BreakException()
                if 'avatar' in data['payload'] and data['payload']['avatar']['event_type'] == 'stream_info':
                    self.avatarLinked = True
                    # 【关键修正】: 传递完整的消息字符串给 PySide6
                    print('avatar ws connected: %s' % str(message))
                    if self.signaler:
                        self.signaler.stream_url_signal.emit(str(message))

                if 'avatar' in data['payload'] and data['payload']['avatar']['event_type'] == 'pong':
                    pass
        except BreakException:
            print('receive error but continue')
        except Exception as e:
            print(e)

class BreakException(Exception):
    """自定义异常类，实现异常退出功能"""
    pass