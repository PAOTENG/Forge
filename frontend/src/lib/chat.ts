/**
 * 对话展示辅助：把工具调用/输出收成 Markdown，给聊天气泡用。
 * SSE 连线与重订阅逻辑主要在 App.vue，这里只管格式。
 */
export function formatToolCall(tool: string, args: string): string {
  /** 【功能】工具名+参数格式化成可折叠的 markdown。输入 tool/args，输出字符串。 */
  const raw = (args || "").trim();
  if (!raw) return `**调用** \`${tool}\``;
  try {
    const parsed = JSON.parse(raw);
    const pretty = JSON.stringify(parsed, null, 2);
    return `**调用** \`${tool}\`\n\n\`\`\`json\n${pretty}\n\`\`\``;
  } catch {
    const lang = tool === "bash" ? "bash" : tool.startsWith("git_") ? "bash" : "text";
    return `**调用** \`${tool}\`\n\n\`\`\`${lang}\n${raw}\n\`\`\``;
  }
}

export function formatToolOutput(output: string): string {
  /** 【功能】工具输出截到 8KB，包进代码块。 */
  const raw = (output || "(ok)").slice(0, 8000);
  return `**工具输出**\n\n\`\`\`\n${raw}\n\`\`\``;
}
