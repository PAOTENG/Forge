# 代码高亮

Forge 默认只打开 Markdown 与 TeX。Python、TypeScript、Vue 等源码需要安装本插件。

## 覆盖的扩展名

`.py` `.ts` `.tsx` `.js` `.jsx` `.vue` `.go` `.rs` `.java` `.c` `.cpp` `.h` `.css` `.html` `.sql` `.toml` `.xml`

## 为什么要单独安装

工作台不想默认把整个仓库当 IDE。插件库可以浏览，只有你点「安装」后才会把插件包写到：

`data/plugins/code/`

然后该会话的文件接口才会允许这些扩展名进入大面板。Agent 侧的 `read_file` 工具不受此限制。

## 不是 Cursor 扩展

不能安装 Open VSX / VS Code Marketplace 上的 `.vsix`。本库是 Forge 自己的查看器包装。若以后要接真实扩展主机，需要单独的加载沙箱，目前没有。
