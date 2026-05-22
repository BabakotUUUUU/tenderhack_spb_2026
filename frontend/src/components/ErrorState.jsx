import styles from './ErrorState.module.css'

export default function ErrorState({ query, warnings = [] }) {
  return (
    <div className={styles.empty}>
      <p>По запросу «{query}» ничего не найдено.</p>
      <p className={styles.hint}>
        Попробуйте проверить написание, выбрать более общую категорию или другой регион.
      </p>
      {warnings.length > 0 && (
        <div className={styles.warnings}>
          {warnings.map(item => (
            <span key={item}>{item}</span>
          ))}
        </div>
      )}
    </div>
  )
}
