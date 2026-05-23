import ProductCard from './ProductCard.jsx'
import styles from './SourceSection.module.css'

const SOURCE_META = {
  yandex_market: { label: 'Яндекс Маркет', color: '#ff9b00', bg: 'rgba(255,155,0,0.08)' },
  ozon: { label: 'Ozon', color: '#005bff', bg: 'rgba(0,91,255,0.08)' },
  wildberries: { label: 'Wildberries', color: '#cb11ab', bg: 'rgba(203,17,171,0.08)' },
  runet: { label: 'Рунет', color: '#00b896', bg: 'rgba(0,184,150,0.08)' },
}

export default function SourceSection({ data }) {
  const meta = SOURCE_META[data.source] || { label: data.source, color: '#888', bg: 'rgba(128,128,128,0.08)' }
  const prices = data.items.map(i => i.price).filter(Boolean)
  const min = prices.length ? Math.min(...prices) : 0
  const max = prices.length ? Math.max(...prices) : 0

  return (
    <section className={styles.section}>
      <div className={styles.header}>
        <div className={styles.titleRow}>
          <div className={styles.sourceBadge} style={{ background: meta.bg, borderColor: meta.color + '44' }}>
            <span className={styles.sourceDot} style={{ background: meta.color }} />
            <span className={styles.sourceName} style={{ color: meta.color }}>{meta.label}</span>
          </div>
          <div className={styles.countBadge}>{data.count} предложений</div>
          {data.status && data.status !== 'ok' && <div className={styles.statusBadge}>{data.status}</div>}
        </div>

        {data.errorReason && <div className={styles.warning}>{data.errorReason}</div>}

        {min > 0 && (
          <div className={styles.priceRange}>
            {min !== max ? (
              <span>от <strong>{fmtPrice(min)}</strong> до <strong>{fmtPrice(max)}</strong></span>
            ) : (
              <span>от <strong>{fmtPrice(min)}</strong></span>
            )}
          </div>
        )}
      </div>

      <div className={styles.grid}>
        {data.items.map((item, idx) => (
          <ProductCard key={`${item.url}-${idx}`} item={item} accentColor={meta.color} />
        ))}
      </div>
    </section>
  )
}

function fmtPrice(v) {
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(v)
}

