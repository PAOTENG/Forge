# 图片预览

安装后可在 Forge 大面板里直接看图片，不必导出到系统看图软件。

## 支持格式

png、jpg、jpeg、gif、webp、svg。

## 安装后发生什么

包文件从 `backend/plugin_store/image/` 复制到 `data/plugins/image/`。会话读文件接口在确认该插件已启用后，会通过 `/blob` 返回原图。

未安装时，点击图片文件只会提示需要插件，不会下载任何东西。
