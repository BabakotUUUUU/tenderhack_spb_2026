import SourceSection from './SourceSection.jsx'
import SkeletonGrid from './SkeletonGrid.jsx'
import styles from './ResultsSection.module.css'

export default function ResultsSection({ results, loading, activeSource }) {
  if (loading) {
    return <SkeletonGrid />
  }

  if (!results) return null

  const filtered = activeSource === 'all'
    ? results.results
    : results.results.filter(r => r.source === activeSource)

  const hasAny = filtered.some(r => r.items.length > 0)

  if (!hasAny) {
    return (
      <div className={styles.noResults}>
        <div className={styles.noResultsIcon}>🔍</div>
        <div className={styles.noResultsTitle}>Ничего не найдено</div>
        <div className={styles.noResultsSub}>Попробуйте изменить запрос или выбрать другой регион</div>
      </div>
    )
  }

  return (
    <div className={styles.wrap}>
      {filtered.map(sourceResult => (
        sourceResult.items.length > 0 && (
          <SourceSection key={sourceResult.source} data={sourceResult} />
        )
      ))}
    </div>
  )
}
