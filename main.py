import sys
import os
import json
import time
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QWidget, QLineEdit, QPushButton, QHBoxLayout)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl, QObject, Signal, Slot, QTimer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings

# 请确保 AipaasAuth.py 和 AvatarWebSocket.py 文件在当前目录下
import AipaasAuth
from AvatarWebSocket import avatarWebsocket, StreamUrlSignaler


# ----------------------------------------------------------------------
# 1. Python QWebChannel 桥接对象
# ----------------------------------------------------------------------
class PyHandler(QObject):
    streamUrlReady = Signal(str)

    def __init__(self, ws_client, parent=None):
        super().__init__(parent)
        self.ws_client = ws_client

    @Slot(str)
    def log(self, msg):
        # 使用 QTimer.singleShot 确保在主线程中安全打印日志
        QTimer.singleShot(0, lambda: print(f"[JS LOG]: {msg}"))

    @Slot(str)
    def sendTextDriver(self, text):
        if self.ws_client and self.ws_client.avatarLinked:
            self.ws_client.sendDriverText(text)
            QTimer.singleShot(0, lambda: print(f"[GUI/JS] Sending driver text: {text}"))
        else:
            QTimer.singleShot(0, lambda: print("[GUI/JS] WebSocket not linked, cannot send driver text."))


class MainWindow(QMainWindow):
    def __init__(self, ws_client, stream_signaler):
        super().__init__()
        self.setWindowTitle("Xunfei Avatar PySide6 Demo")
        self.setGeometry(100, 100, 1280, 720)

        container = QWidget()
        layout = QVBoxLayout(container)
        self.web_view = QWebEngineView()
        page = self.web_view.page()

        # ------------------ 启用开发者工具 (V3) ------------------
        try:
            QWebEngineSettings.defaultSettings().setAttribute(QWebEngineSettings.DeveloperToolsEnabled, True)
        except AttributeError:
            print("[WARNING] QWebEngineSettings.defaultSettings() failed. DevTools may not open.")

        # ------------------ 启用自动播放权限 ------------------
        # 获取当前页面的设置对象
        settings = page.settings()

        # 允许媒体自动播放，不需要用户交互
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)


        def open_devtools_after_load(success):
            if success:
                # 确保 WebChannel 在 DevTools 之前设置
                self.web_view.page().setWebChannel(self.channel)
                print("[GUI] index.html loaded and QWebChannel set successfully (via direct content injection).")

                # 兼容性修复：尝试打开 DevTools 窗口
                try:
                    action = self.web_view.page().action(QWebEnginePage.WebAction.InspectElement)
                    if action:
                        QTimer.singleShot(500, action.trigger)
                        print("[GUI] Attempted to open WebEngine DevTools window.")
                    else:
                        print("[WARNING] WebAction.InspectElement action not found.")
                except AttributeError:
                    print(
                        "[FATAL WARNING] QWebEnginePage lacks WebAction attribute. DevTools cannot be opened programmatically.")

        page.certificateError.connect(self.handle_ssl_error)

        # --- 文件路径和内容读取 ---
        html_file_path = os.path.abspath("test_render.html")
        qwebchannel_path = os.path.abspath("qt/qwebchannel.js")
        js_file_path = os.path.abspath("rtcplayer2.1.3/rtcplayer.umd.js")
        XrtcPlayer_path = "XrtcPlayer.vue"

        html_content = ""
        qwebchannel_content = ""
        rtcplayer_content = ""

        try:
            if not os.path.exists(js_file_path):
                print(f"[FATAL ERROR] 找不到播放器文件！路径: {js_file_path}")
                sys.exit(1)

            with open(html_file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            with open(qwebchannel_path, 'r', encoding='utf-8') as f:
                qwebchannel_content = f.read()
            with open(js_file_path, 'r', encoding='utf-8') as f:
                rtcplayer_content = f.read()

            print(f"[DEBUG] {os.path.basename(js_file_path)} 内容长度: {len(rtcplayer_content)}")

        except Exception as e:
            print(f"[FATAL ERROR] 读取关键文件时发生致命错误: {e}")
            sys.exit(1)

        # --- 核心修正：创建 Try-Catch 包裹内容 ---
        js_script_with_error_capture = f"""
        try {{
            // 注入播放器库的全部内容
            {rtcplayer_content}

            // 强制检查 Interactive 对象是否已定义
            if (typeof Interactive === 'undefined' || typeof Interactive.RTCPlayer === 'undefined') {{
                if (typeof pyHandler !== 'undefined') {{
                     pyHandler.log("【JS ERROR】播放器库执行后，Interactive.RTCPlayer 未被定义。");
                }} else {{
                     console.error("播放器库执行后，Interactive.RTCPlayer 未被定义。");
                }}
            }}

        }} catch(e) {{
            if (typeof pyHandler !== 'undefined') {{
                pyHandler.log("【JS FATAL】播放器库执行失败: " + e.name + ": " + e.message + "\\nStack: " + e.stack);
            }} else {{
                console.error("播放器库执行失败: " + e.message);
            }}
        }}
        """

        # --- 最终注入：替换所有占位符 ---
        modified_html_content = html_content.replace(
            '<script id="qwebchannel-script"></script>',
            f'<script id="qwebchannel-script">\n{qwebchannel_content}\n</script>'
        )
        modified_html_content = modified_html_content.replace(
            '<script id="rtc-player-script"></script>',
            f'<script id="rtc-player-script">\n{js_script_with_error_capture}\n</script>'
        )

        html_abs_path = os.path.abspath(html_file_path)
        html_url = QUrl.fromLocalFile(html_abs_path)
        self.web_view.setHtml(modified_html_content, baseUrl=html_url)

        # --- QWebChannel 初始化 ---
        self.channel = QWebChannel()
        self.py_handler = PyHandler(ws_client)
        self.channel.registerObject('pyHandler', self.py_handler)

        self.web_view.page().loadFinished.connect(open_devtools_after_load)

        # --- 控件和布局 ---
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("在此输入驱动文本...")
        self.send_button = QPushButton("发送文本")
        self.send_button.clicked.connect(lambda: self.py_handler.sendTextDriver(self.text_input.text()))

        control_layout = QHBoxLayout()
        control_layout.addWidget(self.text_input)
        control_layout.addWidget(self.send_button)
        layout.addWidget(self.web_view)
        layout.addLayout(control_layout)
        self.setCentralWidget(container)

        stream_signaler.stream_url_signal.connect(self.on_stream_url_received)

    @Slot(object)
    def handle_ssl_error(self, error):
        if hasattr(error, 'ignoreCertificateError'):
            error.ignoreCertificateError()
            print(f"[WARNING] SSL Certificate Error ignored: {error.description()}")

    # @Slot(str)
    # def on_stream_url_received(self, stream_info_json_string):
    #     """WebRTC 流信息扁平化"""
    #     try:
    #         full_response = json.loads(stream_info_json_string)
    #         avatar_payload = full_response['payload']['avatar']
    #         stream_url_value = avatar_payload['stream_url']
    #         sid_value = avatar_payload.get('cid', '')
    #
    #         # 【最终核心修正】：提取 xrtc 所需的 stream_extend 字段
    #         # 确保将 xrtc 协议所需的入会扩展参数传递给 JS 播放器
    #         stream_extend = avatar_payload.get('stream_extend', {})
    #
    #         stream_info_for_js = {
    #             "playerType": 6,
    #             "sid": sid_value,
    #             "streamUrl": stream_url_value,
    #             "videoSize": {"width": 1280, "height": 720},
    #             # 将 xrtc 扩展参数传递给 JS 播放器
    #             "streamExtend": stream_extend
    #         }
    #
    #         stream_info_json = json.dumps(stream_info_for_js)
    #         self.py_handler.streamUrlReady.emit(stream_info_json)
    #         print(f"[GUI] Successfully processed and sent stream info to JS (WebRTC/XRTC).")
    #
    #     except Exception as e:
    #         print(f"[ERROR] 解析 WebSocket 响应失败: {e}. 请检查 stream_info_json_string 的内容和键名。")
    @Slot(str)
    def on_stream_url_received(self, stream_info_json_string):
        try:
            full_response = json.loads(stream_info_json_string)
            if 'payload' not in full_response or 'avatar' not in full_response['payload']:
                raise ValueError("响应格式错误，缺少 payload.avatar 字段")

            avatar_payload = full_response['payload']['avatar']
            stream_url_value = avatar_payload.get('stream_url', '')
            stream_extend = avatar_payload.get('stream_extend', {})

            if not stream_url_value:
                raise ValueError("stream_url 为空，无法解析服务器地址和房间ID")
            server_url = stream_url_value.rsplit('/', 1)[0]
            room_id = stream_url_value.split('/')[-1]

            user_id = f"user_{int(time.time() * 1000)}"
            time_str = str(int(time.time() * 1000))

            # 修正参数命名，匹配文档和播放器要求
            xrtc_stream_config = {
                "sid": avatar_payload.get('cid', ''),
                "server": server_url.replace('xrtcs://', 'http://'),
                "roomId": room_id,
                "token": stream_extend.get('user_sign', ''),  # 原auth改为token
                "appId": stream_extend.get('appid', ''),  # 驼峰命名
                "userId": user_id,
                "timeStr": time_str
            }

            stream_info_for_js = {
                "playerType": 12,
                "streamUrl": stream_url_value,
                "videoSize": {"width": 1280, "height": 720},
                "xrtcStreamConfig": xrtc_stream_config
            }
            self.py_handler.streamUrlReady.emit(json.dumps(stream_info_for_js))

        except Exception as e:
            print(f"[ERROR] 处理流信息失败: {e}")
# ----------------------------------------------------------------------
# 3. 主程序入口
# ----------------------------------------------------------------------
if __name__ == '__main__':
    # 请替换为您自己的配置信息
    url = 'wss://avatar.cn-huadong-1.xf-yun.com/v1/interact'
    appId = 'c82abf0b'
    appKey = 'c84d26683fa91755f7dff2caa0560d24'
    appSecret = 'MmJmYWE5ZTMxMGY4NjZjMzExNWVlNTUw'
    anchorId = 'cnrfb86h2000000004'
    vcn = 'x4_yezi'

    authUrl = AipaasAuth.assemble_auth_url(url, 'GET', appKey, appSecret)

    app = QApplication(sys.argv)
    stream_signaler = StreamUrlSignaler()

    wsclient = avatarWebsocket(authUrl, protocols='', headers=None, signaler=stream_signaler)
    wsclient.appId = appId
    wsclient.anchorId = anchorId
    wsclient.vcn = vcn

    main_window = MainWindow(wsclient, stream_signaler)
    main_window.show()

    wsclient.start()

    try:
        sys.exit(app.exec())
    except Exception as e:
        print(f"GUI Exited: {e}")
    finally:
        wsclient.stop()