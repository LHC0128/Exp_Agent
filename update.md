# 更新 CodeGraph
codegraph upgrade --check
codegraph upgrade
codegraph sync .
codegraph status

# 更新 Archify
npx.cmd --yes skills@latest add tt-a1i/archify `
  --skill archify --agent codex --copy --yes

# 刷新 Blender MCP
uvx --refresh blender-mcp --help