// fetch wrapper for /api. Every non-2xx response is the contract's error envelope
// {error: {code, message, details}} and is thrown as an ApiError.

export class ApiError extends Error {
  constructor(status, code, message, details = {}) {
    super(message)
    this.status = status
    this.code = code
    this.details = details
  }
}

export async function request(method, path, body) {
  let res
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (e) {
    throw new ApiError(0, 'network', e.message)
  }
  const text = await res.text()
  let json = null
  try {
    json = text ? JSON.parse(text) : null
  } catch {
    // non-JSON body (e.g. proxy error page)
  }
  if (!res.ok) {
    const err = json?.error
    // No envelope from a gateway status = the dev proxy couldn't reach the backend
    if (!err && [502, 503, 504].includes(res.status))
      throw new ApiError(res.status, 'network', res.statusText)
    throw new ApiError(
      res.status,
      err?.code || 'http_error',
      err?.message || res.statusText,
      err?.details,
    )
  }
  return json
}
