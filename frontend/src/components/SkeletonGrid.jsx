import styles from './SkeletonGrid.module.css'

function SkeletonCard() {
  return (
    <div className={styles.card}>
      <div className={`skeleton ${styles.img}`} />
      <div className={styles.body}>
        <div className={`skeleton ${styles.line}`} style={{ width: '85%', height: 14 }} />
        <div className={`skeleton ${styles.line}`} style={{ width: '65%', height: 14 }} />
        <div className={`skeleton ${styles.line}`} style={{ width: '45%', height: 20, marginTop: 8 }} />
      </div>
    </div>
  )
}

export default function SkeletonGrid() {
  return (
    <div className={styles.wrap}>
      {[1, 2, 3, 4].map(section => (
        <div key={section} className={styles.section}>
          <div className={`skeleton ${styles.sectionHeader}`} />
          <div className={styles.grid}>
            {Array.from({ length: 4 }).map((_, i) => (
              <SkeletonCard key={i} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
