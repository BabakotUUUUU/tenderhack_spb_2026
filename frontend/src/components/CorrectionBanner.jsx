import styles from './CorrectionBanner.module.css'

export default function CorrectionBanner({ original, corrected }) {
  if (!corrected) return null
  return (
    <div className={styles.banner}>
      <svg className={styles.icon} viewBox="0 0 24 24" fill="none">
        <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
        <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
      </svg>
      <span>
        Запрос исправлен:{' '}
        <span className={styles.original}>{original}</span>
        {' → '}
        <span className={styles.corrected}>{corrected}</span>
      </span>
    </div>
  )
}
