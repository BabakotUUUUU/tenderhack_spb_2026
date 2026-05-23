import styles from './StatsBar.module.css'

const SOURCE_META = {
  'Яндекс Маркет': { color: '#ff9b00', short: 'ЯМ' },
  'Ozon':           { color: '#005bff', short: 'OZ' },
  'Wildberries':    { color: '#cb11ab', short: 'WB' },
  'Интернет (Рунет)': { color: '#00b896', short: 'WEB' },
}

const STATUS_COLORS = {
  success: '#16a34a',
  partial: '#d97706',
  failed: '#dc2626',
  timeout: '#dc2626',
}

const STATUS_LABELS = {
  success: '',
  partial: 'PARTIAL',
  failed: 'FAILED',
  timeout: 'TIMEOUT',
}

function fmtPrice(v) {
  if (!v) return '—'
  return new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(v)
}

export default function StatsBar({ results, activeSource, onSourceChange }) {
  const total = results.total_items

  return (
    <div className={styles.wrap}>
      <div className={styles.totalBadge}>
        Найдено: <strong>{total}</strong> предложений
      </div>

      <div className={styles.tabs}>
        <button
          className={`${styles.tab} ${activeSource === 'all' ? styles.tabActive : ''}`}
          onClick={() => onSourceChange('all')}
        >
          Все источники
        </button>
        {results.results.map(r => {
          const meta = SOURCE_META[r.source] || { color: '#888', short: '?' }
          return (
            <button
              key={r.source}
              className={`${styles.tab} ${activeSource === r.source ? styles.tabActive : ''}`}
              onClick={() => onSourceChange(r.source)}
            >
              <span className={styles.tabDot} style={{ background: meta.color }} />
              <span className={styles.tabName}>{r.source}</span>
              <span className={styles.tabCount}>{r.total_found}</span>
              {r.status && r.status !== 'success' && (
                <span
                  className={styles.statusBadge}
                  style={{ color: STATUS_COLORS[r.status] || '#888' }}
                >
                  {STATUS_LABELS[r.status] || r.status}
                </span>
              )}
              {r.price_min && (
                <span className={styles.tabPrice}>от {fmtPrice(r.price_min)}</span>
              )}
              {r.price_avg && (
                <span className={styles.tabAvg}>ср. {fmtPrice(r.price_avg)}</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )
}
