import { useEffect, useId, useState, type ReactNode } from 'react'
import { Label, Radio, RadioGroup, Switch } from 'react-aria-components'
import { ApiError, errorMessage, fieldMessage } from '../api/client'
import { useCronPreview, useDeleteDirectory, useDirectories, useEngines, useSaveDirectory, useSettings } from '../api/hooks'
import type { DirectoryIn, DirectoryOut, EngineOut } from '../api/schema.gen'
import { useI18n } from '../i18n'
import { formatDateTime } from '../lib/format'
import { PRESETS, looksLikeCron, presetOf, type Preset } from '../lib/schedule'
import { FolderPicker } from './FolderPicker'
import { Icon } from './Icon'
import { InfoTip } from './InfoTip'
import { Notice } from './ui'
import styles from './DirectoryForm.module.css'

type Engine = DirectoryIn['engine'] & string
type Pace = NonNullable<DirectoryIn['pace']>
type Measure = NonNullable<DirectoryIn['measure']>

const ENGINES: Engine[] = ['native', 'du', 'ncdu', 'gdu']
const CODE = { code: (chunks: ReactNode) => <code>{chunks}</code> }
const PACES: Pace[] = ['max', 'balanced', 'gentle']
const MEASURES: Measure[] = ['apparent', 'disk']
export const DEFAULT_EXCLUDES = ['#snapshot', '.snapshot', '.zfs']

interface FormState {
  name: string
  path: string
  enabled: boolean
  preset: Preset
  custom: string
  engine: Engine
  threads: number
  pace: Pace
  low_priority: boolean
  measure: Measure
  one_filesystem: boolean
  excludes: string[]
}

/** Defaults of a new directory: daily at 03:00, native, 2 threads, balanced, low priority, apparent
 * size, one file system and snapshots excluded. */
export const NEW_DIRECTORY: FormState = {
  name: '', path: '', enabled: true, preset: 'daily', custom: '0 3 * * *', engine: 'native', threads: 2,
  pace: 'balanced', low_priority: true, measure: 'apparent', one_filesystem: true, excludes: DEFAULT_EXCLUDES,
}

function fromDirectory(directory: DirectoryOut): FormState {
  return {
    name: directory.name, path: directory.path, enabled: directory.enabled, preset: presetOf(directory.cron),
    custom: directory.cron ?? '', engine: directory.engine, threads: directory.threads, pace: directory.pace,
    low_priority: directory.low_priority, measure: directory.measure, one_filesystem: directory.one_filesystem,
    excludes: directory.excludes,
  }
}

export function maxThreads(engine: Engine, engines?: EngineOut[]): number {
  return engines?.find(item => item.id === engine)?.max_threads ?? { native: 16, du: 1, ncdu: 16, gdu: 8 }[engine]
}

function cronOf(state: FormState): string | null {
  return state.preset === 'custom' ? state.custom.trim() : PRESETS[state.preset]
}

function Toggle({ label, tip, isSelected, onChange }: { label: string; tip?: ReactNode; isSelected: boolean; onChange: (value: boolean) => void }) {
  const { t } = useI18n()
  return <div className={styles.inline}>
    <Switch isSelected={isSelected} onChange={onChange} className={styles.switch}>
      <span className={styles.track} aria-hidden="true"><span className={styles.thumb} /></span>
      {label}
    </Switch>
    {tip && <InfoTip label={t('form.moreAbout', { name: label })}>{tip}</InfoTip>}
  </div>
}

function FieldError({ code }: { code?: string }) {
  const message = fieldMessage(code)
  return message ? <span className="field-error" role="alert">{message}</span> : null
}

/** Adds or edits a directory: its folder, schedule and scan options. Deleting it keeps the files. */
export function DirectoryForm({ id, onDone, onDeleted }: { id: number | null; onDone: (saved: number | null) => void; onDeleted: () => void }) {
  const { t, rich } = useI18n()
  const directories = useDirectories()
  const engines = useEngines()
  const settings = useSettings()
  const save = useSaveDirectory()
  const remove = useDeleteDirectory()
  const existing = id === null ? undefined : directories.data?.items.find(item => item.directory.id === id)?.directory
  const [state, setState] = useState<FormState | null>(id === null ? NEW_DIRECTORY : null)
  const [picking, setPicking] = useState(id === null)
  const [confirming, setConfirming] = useState(false)
  const [pattern, setPattern] = useState('')
  const formId = useId()

  useEffect(() => {
    if (state === null && existing) setState(fromDirectory(existing))
  }, [existing, state])

  const cron = state ? cronOf(state) : null
  const preview = useCronPreview(state && cron && looksLikeCron(cron) ? cron : null)

  if (id !== null && directories.data && !existing) return <Notice error>{t('directory.notFound')}</Notice>
  if (!state) return <p className="muted">{t('shell.loading')}</p>
  const form = state
  const update = (changes: Partial<FormState>) => setState({ ...form, ...changes })
  const engineList = engines.data?.items
  const limit = maxThreads(form.engine, engineList)
  const fields = save.error instanceof ApiError ? save.error.fields : {}

  function submit() {
    const threads = Math.min(Math.max(1, Math.round(form.threads) || 1), limit)
    save.mutate({
      id,
      body: {
        name: form.name, path: form.path, enabled: form.enabled, cron, engine: form.engine, threads, pace: form.pace,
        low_priority: form.low_priority, measure: form.measure, one_filesystem: form.one_filesystem, excludes: form.excludes,
      },
    }, { onSuccess: result => onDone(result.directory.id) })
  }

  function addPattern() {
    const value = pattern.trim()
    if (value && !form.excludes.includes(value)) update({ excludes: [...form.excludes, value] })
    setPattern('')
  }

  return <form className={styles.form} aria-labelledby={`${formId}-title`} onSubmit={event => { event.preventDefault(); submit() }}>
    <div className={styles.formHead}>
      <button type="button" className="button quiet small" onClick={() => onDone(null)}>
        <Icon name="back" size={16} />{t('common.back')}
      </button>
      <h3 id={`${formId}-title`}>{id === null ? t('form.addTitle') : t('form.editTitle', { name: existing?.name ?? '' })}</h3>
    </div>

    <fieldset className={styles.section}>
      <legend>{t('form.sections.folder')}</legend>
      <div className="field">
        <label htmlFor={`${formId}-path`}>{t('form.path')}</label>
        <div className={styles.pathRow}>
          <input id={`${formId}-path`} type="text" className="mono" value={form.path} placeholder="/data/…"
            aria-invalid={Boolean(fields.path)} onChange={event => update({ path: event.target.value })} />
          <button type="button" className="button secondary small" aria-expanded={picking} onClick={() => setPicking(open => !open)}>
            <Icon name="folder" size={16} />{t('form.browse')}
          </button>
        </div>
        <span className="field-hint">{t('form.pathHint')}</span>
        <FieldError code={fields.path} />
      </div>
      {picking && <FolderPicker start={form.path || null} onCancel={() => setPicking(false)} onPick={path => {
        const name = form.name || path.split('/').filter(Boolean).pop() || path
        update({ path, name })
        setPicking(false)
      }} />}
      <div className="field">
        <label htmlFor={`${formId}-name`}>{t('form.name')}</label>
        <input id={`${formId}-name`} type="text" value={form.name} maxLength={60} aria-invalid={Boolean(fields.name)}
          onChange={event => update({ name: event.target.value })} />
        <FieldError code={fields.name} />
      </div>
    </fieldset>

    <fieldset className={styles.section}>
      <legend>{t('form.sections.schedule')}</legend>
      <Toggle label={t('form.enabled')} tip={t('tips.enabled')} isSelected={form.enabled} onChange={enabled => update({ enabled })} />
      <div className="field">
        <span className="field-label">
          <label htmlFor={`${formId}-preset`}>{t('form.schedule')}</label>
          <InfoTip label={t('form.moreAbout', { name: t('form.schedule') })}>{t('tips.schedule')}</InfoTip>
        </span>
        <select id={`${formId}-preset`} value={form.preset} onChange={event => update({ preset: event.target.value as Preset })}>
          {(['manual', 'hourly', 'every6h', 'daily', 'weekly', 'custom'] as const).map(preset =>
            <option key={preset} value={preset}>{t(`schedule.presets.${preset}`)}</option>)}
        </select>
      </div>
      {form.preset === 'custom' && <div className="field">
        <label htmlFor={`${formId}-cron`}>{t('form.cron')}</label>
        <input id={`${formId}-cron`} type="text" className="mono" value={form.custom} placeholder="0 3 * * *"
          aria-invalid={Boolean(fields.cron) || Boolean(preview.error)} aria-describedby={`${formId}-cron-hint`}
          onChange={event => update({ custom: event.target.value })} />
        <span id={`${formId}-cron-hint`} className="field-hint">{t('form.cronHint')}</span>
        <FieldError code={fields.cron} />
      </div>}
      {cron && <div className={styles.preview} aria-live="polite">
        {preview.error || (form.preset === 'custom' && !looksLikeCron(cron))
          ? <span className="field-error">{t('errors.codes.invalid_cron')}</span>
          : preview.data && <>
            <span className="muted small">{t('form.nextRuns', { timezone: preview.data.timezone })}</span>
            <ul>{preview.data.next_runs.map(run => <li key={run}>{formatDateTime(run, undefined, preview.data?.timezone)}</li>)}</ul>
          </>}
      </div>}
      {cron === null && <p className="muted small">{t('form.manualHint')}</p>}
    </fieldset>

    <fieldset className={styles.section}>
      <legend>{t('form.sections.method')}</legend>
      <RadioGroup value={form.engine} onChange={value => {
        const engine = value as Engine
        update({ engine, threads: Math.min(form.threads, maxThreads(engine, engineList)) })
      }} className={styles.options} aria-describedby={fields.engine ? `${formId}-engine-error` : undefined}>
        <Label className="field-label">{t('form.engine')}</Label>
        {ENGINES.map(engine => {
          const info = engineList?.find(item => item.id === engine)
          const available = info?.available ?? engine === 'native'
          return <div key={engine} className={styles.option}>
            <Radio value={engine} isDisabled={!available} className={styles.radio}>
              <span className={styles.mark} aria-hidden="true" />
              <span className={styles.optionText}>
                <strong>{t(`engines.${engine}.name`)}</strong>
                <span className="small muted">{t(`engines.${engine}.summary`)}{' · '}
                  {available ? info?.version ?? '' : t('form.notInstalled')}</span>
              </span>
            </Radio>
            <InfoTip label={t('form.moreAbout', { name: t(`engines.${engine}.name`) })}>{t(`tips.engines.${engine}`)}</InfoTip>
          </div>
        })}
      </RadioGroup>
      {fields.engine && <span id={`${formId}-engine-error`}><FieldError code={fields.engine} /></span>}
      <div className="field">
        <span className="field-label">
          <label htmlFor={`${formId}-threads`}>{t('form.threads')}</label>
          <InfoTip label={t('form.moreAbout', { name: t('form.threads') })}>{t('tips.threads')}</InfoTip>
        </span>
        <input id={`${formId}-threads`} type="number" min={1} max={limit} step={1} className={styles.number}
          value={limit === 1 ? 1 : form.threads} disabled={limit === 1} aria-describedby={`${formId}-threads-hint`}
          aria-invalid={Boolean(fields.threads)} onChange={event => update({ threads: Number(event.target.value) })} />
        <span id={`${formId}-threads-hint`} className="field-hint">
          {limit === 1 ? t('form.threadsSingle', { engine: t(`engines.${form.engine}.name`) }) : t('form.threadsRange', { max: limit })}
        </span>
        <FieldError code={fields.threads} />
      </div>
      <RadioGroup value={form.pace} onChange={value => update({ pace: value as Pace })} className={styles.options}
        aria-describedby={`${formId}-pace-note`}>
        <Label className="field-label">{t('form.pace')}</Label>
        <div className={styles.row}>
          {PACES.map(pace => <div key={pace} className={styles.option}>
            <Radio value={pace} className={styles.radio}>
              <span className={styles.mark} aria-hidden="true" />
              <span className={styles.optionText}><strong>{t(`form.paces.${pace}.name`)}</strong></span>
            </Radio>
            <InfoTip label={t('form.moreAbout', { name: t(`form.paces.${pace}.name`) })}>{t(`tips.paces.${pace}`)}</InfoTip>
          </div>)}
        </div>
        <span id={`${formId}-pace-note`} className="field-hint">{t('tips.paceNote')}</span>
      </RadioGroup>
      <Toggle label={t('form.lowPriority')} tip={t('tips.lowPriority')} isSelected={form.low_priority}
        onChange={low_priority => update({ low_priority })} />
      <RadioGroup value={form.measure} onChange={value => update({ measure: value as Measure })} className={styles.options}>
        <Label className="field-label">{t('form.measure')}</Label>
        <div className={styles.row}>
          {MEASURES.map(measure => <div key={measure} className={styles.option}>
            <Radio value={measure} className={styles.radio}>
              <span className={styles.mark} aria-hidden="true" />
              <span className={styles.optionText}><strong>{t(`form.measures.${measure}.name`)}</strong></span>
            </Radio>
            <InfoTip label={t('form.moreAbout', { name: t(`form.measures.${measure}.name`) })}>{t(`tips.measures.${measure}`)}</InfoTip>
          </div>)}
        </div>
      </RadioGroup>
      <Toggle label={t('form.oneFilesystem')} tip={rich('tips.oneFilesystem', CODE)} isSelected={form.one_filesystem}
        onChange={one_filesystem => update({ one_filesystem })} />
      <div className="field">
        <span className="field-label">
          <label htmlFor={`${formId}-exclude`}>{t('form.excludes')}</label>
          <InfoTip label={t('form.moreAbout', { name: t('form.excludes') })}>{rich('tips.excludes', CODE)}</InfoTip>
        </span>
        {form.excludes.length > 0 && <ul className={styles.chips} aria-label={t('form.excludesList')}>
          {form.excludes.map(value => <li key={value} className={styles.chip}>
            <code>{value}</code>
            <button type="button" className={styles.chipRemove} aria-label={t('common.remove', { name: value })}
              onClick={() => update({ excludes: form.excludes.filter(item => item !== value) })}>
              <Icon name="close" size={14} />
            </button>
          </li>)}
        </ul>}
        <div className={styles.pathRow}>
          <input id={`${formId}-exclude`} type="text" className="mono" value={pattern} placeholder="*.tmp"
            aria-invalid={Boolean(fields.excludes)} onChange={event => setPattern(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addPattern() } }} />
          <button type="button" className="button secondary small" disabled={!pattern.trim()} onClick={addPattern}>
            <Icon name="plus" size={16} />{t('common.add')}
          </button>
        </div>
        <FieldError code={fields.excludes} />
      </div>
    </fieldset>

    {save.error && <Notice error>{errorMessage(save.error)}</Notice>}
    {remove.error && <Notice error>{errorMessage(remove.error)}</Notice>}
    <div className={styles.actions}>
      <button type="submit" className="button" disabled={save.isPending || !form.name.trim() || !form.path.trim()}>
        {save.isPending ? t('common.saving') : id === null ? t('form.addSubmit') : t('common.save')}
      </button>
      <button type="button" className="button secondary" onClick={() => onDone(null)}>{t('common.cancel')}</button>
      {id !== null && !confirming && <button type="button" className={`button danger ${styles.delete}`} onClick={() => setConfirming(true)}>
        <Icon name="trash" size={16} />{t('common.delete')}
      </button>}
    </div>
    {id !== null && confirming && <div className={`notice ${styles.confirm}`} role="alertdialog" aria-labelledby={`${formId}-confirm`}>
      <p id={`${formId}-confirm`}>{t('form.deleteConfirm', { name: existing?.name ?? '' })}</p>
      <div className={styles.actions}>
        <button type="button" className="button danger" disabled={remove.isPending}
          onClick={() => remove.mutate(id, { onSuccess: onDeleted })}>{t('form.deleteSubmit')}</button>
        <button type="button" className="button secondary" onClick={() => setConfirming(false)}>{t('common.cancel')}</button>
      </div>
    </div>}
    {settings.data && <p className="muted small">{t('form.serverTime', { timezone: settings.data.timezone })}</p>}
  </form>
}
