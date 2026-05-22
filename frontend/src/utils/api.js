const BASE = import.meta.env.VITE_API_URL || '/api'

export async function searchProducts(query, region = 'Москва', limit = 12) {
  const params = new URLSearchParams({ q: query, region, limit })
  const resp = await fetch(`${BASE}/search?${params}`)
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
