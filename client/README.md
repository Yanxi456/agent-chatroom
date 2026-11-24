# 智能聊天室网页客户端

在仓库的 client 目录运行 npm install。打开三个终端分别启动 TCP 服务端、网关和 Vue 页面。TCP 服务由后端提供，网关默认连接 127.0.0.1:8765。

```powershell
npm run gateway
```

```powershell
npm run dev
```

打开 Vite 打印的本地地址。网页通过同站点 /ws 连接网关，Vite 将其转发到 127.0.0.1:8766。可通过 CHAT_HOST 和 CHAT_PORT 设置网关的 TCP 上游。默认允许本机网页来源，CHAT_ORIGIN 可指定单一来源。

```powershell
npm test
npm run build
```

自动化测试使用临时 WebSocket 网关和模拟 TCP 端点，验证 UTF-8 请求、拆帧、合帧及连接释放。Agent、历史和多用户业务联调需要运行真实后端。
