# Error Handling
- MCP server unreachable: inform user which service is unavailable
- TME error/rate limit: fall back to web search, inform user about reduced data quality
- API timeout: retry with escalating timeouts, max 3 attempts
- Never return empty response -- always explain what happened
