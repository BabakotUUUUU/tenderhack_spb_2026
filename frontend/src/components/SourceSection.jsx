import ProductCard from './ProductCard.jsx'
import styles from './SourceSection.module.css'

const SOURCE_META = {
  'Яндекс Маркет':    { color: '#ff9b00', bg: 'rgba(255,155,0,0.08)', emoji: '🟡' },
  'Ozon':             { color: '#005bff', bg: 'rgba(0,91,255,0.08)',   emoji: '🔵' },
  'Wildberries':      { color: '#cb11ab', bg: 'rgba(203,17,171,0.08)', emoji: '🟣' },
  'Интернет (Рунет)': { color: '#00e5b0', bg: 'rgba(0,229,176,0.08)', emoji: '🌐' },
}

function fmtPrice(v) {
  if (!v) return null
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency', currency: 'RUB', maximumFractionDigits: 0
  }).format(v)
}

export default function SourceSection({ data }) {
  const meta = SOURCE_META[data.source] || { color: '#888', bg: 'rgba(128,128,128,0.08)', emoji: '🔎' }
  const prices = [data.price_min, data.price_max].filter(Boolean)

  return (
    <section className={styles.section}>
      <div className={styles.header}>
        <div className={styles.titleRow}>
          <div className={styles.sourceBadge} style={{ background: meta.bg, borderColor: meta.color + '44' }}>
            <span className={styles.sourceDot} style={{ background: meta.color }} />
            <span className={styles.sourceName} style={{ color: meta.color }}>{data.source}</span>
          </div>
          <div className={styles.countBadge}>
            {data.total_found} {plural(data.total_found, ['предложение', 'предложения', 'предложений'])}
          </div>
        </div>

        {prices.length > 0 && (
          <div className={styles.priceRange}>
            {data.price_min && data.price_max && data.price_min !== data.price_max ? (
              <span>от <strong>{fmtPrice(data.price_min)}</strong> до <strong>{fmtPrice(data.price_max)}</strong></span>
            ) : (
              <span>от <strong>{fmtPrice(data.price_min || data.price_max)}</strong></span>
            )}
            {data.price_avg && (
              <span className={styles.avgPrice}>· средняя {fmtPrice(data.price_avg)}</span>
            )}
          </div>
        )}
      </div>

      <div className={styles.grid}>
        {data.items.map((item, idx) => (
          <ProductCard key={idx} item={item} accentColor={meta.color} />
        ))}
      </div>
    </section>
  )
}

function plural(n, forms) {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod100 >= 11 && mod100 <= 19) return forms[2]
  if (mod10 === 1) return forms[0]
  if (mod10 >= 2 && mod10 <= 4) return forms[1]
  return forms[2]
}
