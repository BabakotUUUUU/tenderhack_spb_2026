import styles from './Header.module.css'

export default function Header() {
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <div className={styles.logo}>
          <span className={styles.logoIcon}>P</span>
          <div>
            <div className={styles.logoTitle}>PriceHunt</div>
            <div className={styles.logoSub}>Поиск цен в открытых источниках</div>
          </div>
        </div>
        <div className={styles.badge}>Tender Hack · СПБ 2026</div>
      </div>
    </header>
  )
}
