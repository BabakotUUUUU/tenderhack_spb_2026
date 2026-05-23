const BASE = import.meta.env.VITE_API_URL || '/api'

function inferCategory(query) {
  const q = query.toLowerCase()
  if (/шины|резина|покрыш|колеса|\d{3}[ /-]?\d{2}/.test(q)) return 'tires'
  if (/принтер|мфу|сканер|canon|hp|xerox|epson|оргтех/.test(q)) return 'office'
  return 'clothes'
}

export async function searchProducts(query, region = 'Москва', limit = 10) {
  const resp = await fetch(`${BASE}/search`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, category: inferCategory(query), region, limit }),
  })
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}))
    throw new Error(err.detail || `Ошибка сервера: ${resp.status}`)
  }
  return resp.json()
}

export async function fetchSuggest(q) {
  const params = new URLSearchParams({ q })
  const resp = await fetch(`${BASE}/search/suggest?${params}`)
  if (!resp.ok) return []
  return (await resp.json()).suggestions || []
}

