# JSON 查看器

Forge 插件库中的可选查看器。安装后才会复制到本机 `data/plugins/json/`，未安装时只能在库里浏览。

## 作用

打开工作区中的 `.json` 文件，使用 Monaco 做语法高亮。大文件会截断显示。

## 扩展名

- `.json`

## 安装位置

点击「安装」后，后端把本插件包从插件库拷贝到：

`data/plugins/json/`

其中包含 `manifest.json` 与本说明。卸载会删除该目录并去掉数据库中的启用记录。

## 说明

这不是 VS Code / Cursor 市场里的扩展。它只解锁 Forge 工作台里已经有的 JSON 编辑器。不能在沙箱里执行第三方脚本。
