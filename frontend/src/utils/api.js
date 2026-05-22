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
