import { useEffect, useId, useState } from 'react'
import { ApiError, errorMessage, fieldMessage } from '../api/client'
import { useSaveSettings, useSettings, useVersion } from '../api/hooks'
import type { SettingsIn } from '../api/schema.gen'
import { setLanguagePreference, storedPreference, useI18n, type LanguagePreference } from '../i18n'
import { InfoTip } from './InfoTip'
import { Notice } from './ui'
import styles from './DirectoryForm.module.css'

const FIELDS = [
  { key: 'capacity_interval_minutes', min: 1, max: 1440 },
  { key: 'keep_scans', min: 1, max: 1000 },
  { key: 'min_entry_mib', min: 1, max: 1024 },
  { key: 'warn_percent', min: 1, max: 99 },
  { key: 'critical_percent', min: 2, max: 100 },
] as const

/** Capacity sampling, history kept, the smallest folder stored, thresholds and the language. */
export function GeneralSettings() {
  const { t } = useI18n()
  const settings = useSettings()
  const version = useVersion()
  const save = useSaveSettings()
  const [values, setValues] = useState<SettingsIn | null>(null)
  const [language, setLanguage] = useState<LanguagePreference>(storedPreference)
  const [saved, setSaved] = useState(false)
  const id = useId()

  useEffect(() => {
    if (settings.data && values === null) {
      const { timezone: _timezone, ...rest } = settings.data
      setValues(rest)
    }
  }, [settings.data, values])

  if (settings.error) return <Notice error>{errorMessage(settings.error)}</Notice>
  if (!values) return <p className="muted">{t('shell.loading')}</p>
  const form = values
  const fields = save.error instanceof ApiError ? save.error.fields : {}

  return <form className={styles.form} onSubmit={event => {
    event.preventDefault()
    setSaved(false)
    save.mutate(form, { onSuccess: () => setSaved(true) })
  }}>
    <fieldset className={styles.section}>
      <legend>{t('general.scans')}</legend>
      {FIELDS.map(field => <div className="field" key={field.key}>
        <span className="field-label">
          <label htmlFor={`${id}-${field.key}`}>{t(`general.fields.${field.key}.label`)}</label>
          <InfoTip label={t('form.moreAbout', { name: t(`general.fields.${field.key}.label`) })}>
            {t(`general.fields.${field.key}.tip`)}
          </InfoTip>
        </span>
        <input id={`${id}-${field.key}`} type="number" className={styles.number} min={field.min} max={field.max} step={1}
          value={form[field.key]} aria-invalid={Boolean(fields[field.key])}
          onChange={event => setValues({ ...form, [field.key]: Number(event.target.value) })} />
        {fields[field.key] && <span className="field-error">{fieldMessage(fields[field.key])}</span>}
      </div>)}
      <p className="muted small">{t('general.timezone', { timezone: settings.data?.timezone ?? '' })}</p>
    </fieldset>
    {save.error && <Notice error>{errorMessage(save.error)}</Notice>}
    {saved && <Notice>{t('general.saved')}</Notice>}
    <div className={styles.actions}>
      <button type="submit" className="button" disabled={save.isPending}>{save.isPending ? t('common.saving') : t('common.save')}</button>
    </div>
    <fieldset className={styles.section}>
      <legend>{t('general.interface')}</legend>
      <div className="field">
        <label htmlFor={`${id}-language`}>{t('general.language')}</label>
        <select id={`${id}-language`} value={language} onChange={event => {
          const next = event.target.value as LanguagePreference
          setLanguage(next)
          setLanguagePreference(next)
        }}>
          <option value="auto">{t('general.languages.auto')}</option>
          <option value="en">English</option>
          <option value="es">Español</option>
        </select>
        <span className="field-hint">{t('general.languageHint')}</span>
      </div>
    </fieldset>
    {version.data && <p className="muted small">{t('general.version', { version: version.data.version })}</p>}
  </form>
}
