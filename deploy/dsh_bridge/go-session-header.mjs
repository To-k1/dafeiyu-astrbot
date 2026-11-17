export const name = "fishhome-go-session-header"

export function apply(ctx) {
  ctx.on("llm-pi-ai/request-headers", (request, _headers, next) => {
    const headers = next()
    if (request.provider === "opencode-go") {
      const session = String(request.sessionId ?? "")
      if (!/^[A-Za-z0-9_-]{1,128}$/.test(session)) throw new Error("Go requires the actual DSH session id")
      headers.set("x-opencode-session", session)
    }
    return headers
  })
}
