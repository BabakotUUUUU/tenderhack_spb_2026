import styles from './Header.module.css'

const SOURCES = [
  { name: 'Яндекс Маркет', color: '#ff9b00' },
  { name: 'Ozon', color: '#005bff' },
  { name: 'Wildberries', color: '#cb11ab' },
  { name: 'Рунет', color: '#00e5b0' },
]

export default function Header() {
  return (
    <header className={styles.header}>
      <div className={styles.inner}>
        <div className={styles.logo}>
          <div className={styles.logoMark}>
            <span className={styles.logoP}>P</span>
          </div>
          <div>
            <div className={styles.logoName}>PriceHunt</div>
            <div className={styles.logoSub}>поиск цен</div>
          </div>
        </div>

        <div className={styles.sources}>
          {SOURCES.map(s => (
            <div key={s.name} className={styles.sourceChip}>
              <span className={styles.sourceDot} style={{ background: s.color }} />
              {s.name}
            </div>
          ))}
        </div>
      </div>
    </header>
  )
}
