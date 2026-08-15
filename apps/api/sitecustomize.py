# 全局代理补丁（容器启动时由 Python 自动 import）。
# 让 urllib（llm.py / embedding.py 里的阻塞式 urlopen）默认走 mihomo 出口，
# 解决容器内访问 DeepSeek / SiliconFlow 等境外 LLM 不通的问题。
# 仅当运行时传入 HTTPS_PROXY / https_proxy 才生效；否则 urllib 走直连，不影响内网。
import os
import urllib.request

_PROXY = (
    os.environ.get("https_proxy")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("http_proxy")
)
if _PROXY:
    urllib.request.install_opener(
        urllib.request.build_opener(urllib.request.ProxyHandler({"https": _PROXY}))
    )
