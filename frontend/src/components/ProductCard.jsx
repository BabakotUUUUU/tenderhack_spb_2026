import { useState } from 'react'
import styles from './ProductCard.module.css'

function fmtPrice(v) {
  if (!v) return null
  return new Intl.NumberFormat('ru-RU', {
    style: 'currency', currency: 'RUB', maximumFractionDigits: 0
  }).format(v)
}

export default function ProductCard({ item, accentColor }) {
  const [imgError, setImgError] = useState(false)
  const [expanded, setExpanded] = useState(false)

  const allChars = Object.entries(item.characteristics || {})
  const chars = expanded ? allChars : allChars.slice(0, 4)
  const hasChars = allChars.length > 0

  return (
    <article className={styles.card} style={{ '--card-accent': accentColor }}>
      {/* Image */}
      <div className={styles.imgWrap}>
        {item.image_url && !imgError ? (
          <img
            className={styles.img}
            src={item.image_url}
            alt={item.title}
            onError={() => setImgError(true)}
            loading="lazy"
          />
        ) : (
          <div className={styles.imgPlaceholder}>
            <svg viewBox="0 0 24 24" fill="none" width="32" height="32">
              <rect x="3" y="3" width="18" height="18" rx="3" stroke="currentColor" strokeWidth="1.5"/>
              <circle cx="8.5" cy="8.5" r="1.5" stroke="currentColor" strokeWidth="1.5"/>
              <path d="M21 15l-5-5L5 21" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
            </svg>
          </div>
        )}

        {item.rating && (
          <div className={styles.ratingBadge}>
            <svg viewBox="0 0 16 16" width="10" height="10">
              <path d="M8 1l1.8 3.6L14 5.5l-3 2.9.7 4.1L8 10.4l-3.7 2.1.7-4.1-3-2.9 4.2-.9L8 1z" fill="currentColor"/>
            </svg>
            {item.rating.toFixed(1)}
            {item.reviews_count > 0 && (
              <span className={styles.reviewsCount}>{item.reviews_count} отз.</span>
            )}
          </div>
        )}
        {item.relevance_score > 0.5 && (
          <div className={styles.relevanceBadge}>
            {Math.round(item.relevance_score * 100)}%
          </div>
        )}
      </div>

      {/* Content */}
      <div className={styles.body}>
        <div className={styles.title} title={item.title}>
          {item.title}
        </div>

        {/* Characteristics */}
        {hasChars && (
          <div className={`${styles.chars} ${expanded ? styles.charsExpanded : ''}`}>
            {chars.map(([k, v]) => (
              <div key={k} className={styles.charRow}>
                <span className={styles.charKey}>{k}</span>
                <span className={styles.charVal}>{v}</span>
              </div>
            ))}
          </div>
        )}

        {allChars.length > 4 && (
          <button className={styles.expandBtn} onClick={() => setExpanded(e => !e)}>
            {expanded ? '↑ Скрыть' : `↓ Ещё ${allChars.length - 4}`}
          </button>
        )}

        {/* Price + Link */}
        <div className={styles.footer}>
          <div className={styles.price}>
            {item.price ? fmtPrice(item.price) : <span className={styles.priceUnknown}>Цена не указана</span>}
          </div>
          {item.product_url && (
            <a
              href={item.product_url}
              target="_blank"
              rel="noopener noreferrer"
              className={styles.link}
            >
              <svg viewBox="0 0 24 24" fill="none" width="12" height="12">
                <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <polyline points="15 3 21 3 21 9" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                <line x1="10" y1="14" x2="21" y2="3" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
              Открыть источник
            </a>
          )}
        </div>
      </div>
    </article>
  )
}
