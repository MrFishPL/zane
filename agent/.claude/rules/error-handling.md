# Error Handling
- MCP server unreachable: inform user which service is unavailable
- Nexar error/quota exceeded: fall back to web search, inform user about reduced data quality
- LiteLLM timeout: retry with exponential backoff, max 3 attempts
- Never return empty response -- always explain what happened
