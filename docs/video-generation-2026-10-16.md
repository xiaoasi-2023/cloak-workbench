# Dola 视频生成

新增主窗口内的「视频生成」页，左侧创作设置、右侧任务进度和结果。环境列表显示绑定邮箱与运行状态，复用原环境的登录、指纹和代理。可同时处理三个不同环境，每个环境只允许一个视频任务，和自动 Google 登录互斥。

参考本地 `dola-pool` 的两处协议形状：`extensions/dola30` 中实时技能配置和 action bar 的时长选项、`protocol/dola_pure_api.py` 中 `chat_ability` 的模型 / duration / ratio 参数。没有复制第三方整个扩展，也没有引入滑块求解、媒体解密、请求签名或 15+15 拼接实现。

每次任务新建 Dola 标签页，拦截并扩展此标签页实时返回的视频配置；前端保持原来的签名流程。最终请求必须同时匹配视频能力 17、提示词、明确选择的模型、时长和比例，才发送一次。请求体不改写；错误、未知结果、取消、重启均不自动重发。30 秒选项在界面明确选择 Seedance 2.5；2.0 仅 5 / 10 / 15 秒。

读取专用会话中视频的媒体元数据，只有实际时长有效才标记完成。普通视频 URL 可从当前会话的 `/im/chain/single` 响应识别，在同一浏览器中加载元数据，沿用当前代理；加密 URL 不做解密。偏差超过 1 秒标记时长不符。元数据无法取得时继续等待，超时保留原会话入口，不把请求时长当成片时长。

关闭软件不会把已提交任务当成上游取消；重启后保留中断记录，可在原环境打开原会话确认结果。进程内监测可停止，历史记录可删除，两者均不删除 Dola 网站作品。失败、停止或超时会关闭专用标签页，防止解除拦截后网页定时器再次提交；已经取得数字会话地址时可以重新打开原会话，未取得时需从 Dola 历史记录确认。生成额度由 Dola 服务端决定，界面明确标注消耗额度。

监测期间只对任务标签页启用 CDP 焦点模拟，防止原生浏览器窗口在管理页被隐藏后暂停媒体元数据加载；监测结束恢复默认焦点状态。这不切换工作台当前页面，也不激活桌面窗口。

隔离验证使用真实 CloakBrowser、真实 WPF 控件、真实 CDP 拦截和实际 15 / 30 秒 WebM 元数据；测试传输层拦住所有请求，没有向 Dola 发送生成请求。真实账号对 15 / 30 秒、模型与地区的支持仍需用户发起实际生成后确认。

验证入口：

- `.venv/Scripts/python.exe -m unittest discover -s worker -p "test_*.py" -q`
- `.venv/Scripts/python.exe tests/video_flow_probe.py`
- `.tools/dotnet/dotnet.exe build tests/proxy_ui_harness/ProxyUiHarness.csproj -o .local/proxy-ui-build --nologo`
- `.venv/Scripts/python.exe tests/video_ui_probe.py`

媒体夹具利用本机 Playwright 附带的 ffmpeg 与仓库内的单色 JPEG 生成，不属于生产依赖。
