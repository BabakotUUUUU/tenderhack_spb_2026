import { useState, useCallback } from 'react'
import SearchBar from './components/SearchBar.jsx'
import ResultsSection from './components/ResultsSection.jsx'
import Header from './components/Header.jsx'
import CorrectionBanner from './components/CorrectionBanner.jsx'
import StatsBar from './components/StatsBar.jsx'
import styles from './App.module.css'
import { searchProducts } from './utils/api.js'

export default function App() {
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState(null)
  const [error, setError] = useState(null)
  const [activeSource, setActiveSource] = useState('all')

  const handleSearch = useCallback(async ({ query, region }) => {
    setLoading(true)
    setError(null)
    setResults(null)
    setActiveSource('all')
    try {
      const data = await searchProducts(query, region)
      setResults(data)
    } catch (e) {
      setError(e.message || 'Ошибка при поиске')
    } finally {
      setLoading(false)
    }
  }, [])

  return (
    <div className={styles.app}>
      <div className={styles.bg}>
        <div className={styles.bgGrad1} />
        <div className={styles.bgGrad2} />
        <div className={styles.bgGrid} />
      </div>

      <Header />

      <main className={styles.main}>
        <section className={styles.hero}>
          <div className={styles.heroTag}>Tender Hack · Санкт-Петербург</div>
          <h1 className={styles.heroTitle}>
            Найди лучшую<br />
            <span className={styles.heroAccent}>цену</span> сейчас
          </h1>
          <p className={styles.heroSub}>
            Поиск по Яндекс Маркету, Ozon, Wildberries и открытому Рунету
          </p>
          <SearchBar onSearch={handleSearch} loading={loading} />
        </section>

        {results?.was_corrected && (
          <CorrectionBanner
            original={results.original_query}
            corrected={results.corrected_query}
          />
        )}

        {results && !loading && (
          <StatsBar
            results={results}
            activeSource={activeSource}
            onSourceChange={setActiveSource}
          />
        )}

        {error && (
          <div className={styles.error}>
            <span className={styles.errorIcon}>⚠</span>
            {error}
          </div>
        )}

        {(results || loading) && (
          <ResultsSection
            results={results}
            loading={loading}
            activeSource={activeSource}
          />
        )}

        {!results && !loading && (
          <div className={styles.emptyState}>
            <div className={styles.emptyGrid}>
              {['Одежда', 'Шины', 'Ноутбук', 'Принтер', 'Кроссовки', 'МФУ'].map(hint => (
                <button
                  key={hint}
                  className={styles.hintChip}
                  onClick={() => handleSearch({ query: hint, region: 'Москва' })}
                >
                  {hint}
                </button>
              ))}
            </div>
          </div>
        )}
      </main>

      <footer className={styles.footer}>
        <span>PriceHunt · Tender Hack СПБ 2026</span>
        <span className={styles.footerDot} />
        <span>Портал Поставщиков · ДИТ Москвы</span>
      </footer>
    </div>
  )
}
