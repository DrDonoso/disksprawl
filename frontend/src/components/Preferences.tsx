import { Button, Menu, MenuItem, MenuTrigger, Popover } from 'react-aria-components'
import { LANGUAGES, setLanguage, useI18n, type Language } from '../i18n'
import { THEME_PREFERENCES, setThemePreference, useThemePreference, type ThemePreference } from '../lib/theme'
import { Icon, type IconName } from './Icon'
import styles from './Preferences.module.css'

/** Each language by its own name. */
const LANGUAGE_NAMES: Readonly<Record<Language, string>> = { en: 'English', es: 'Español' }
const THEME_ICONS: Readonly<Record<ThemePreference, IconName>> = { light: 'sun', dark: 'moon', auto: 'monitor' }

interface Option<K extends string> {
  id: K
  label: string
  icon?: IconName
  lang?: string
}

/** An icon button that opens a menu of options, with the current one checked. */
function OptionsMenu<K extends string>({ label, icon, options, value, onChange }: {
  label: string
  icon: IconName
  options: readonly Option<K>[]
  value: K
  onChange: (value: K) => void
}) {
  return <MenuTrigger>
    {/* The wrapper's title shows the name on hover, like the other buttons of the bar. */}
    <span className={styles.trigger} title={label}>
      <Button className="icon-button" aria-label={label}><Icon name={icon} /></Button>
    </span>
    <Popover placement="bottom end" offset={4} className={styles.popover}>
      <Menu className={styles.menu} selectionMode="single" disallowEmptySelection selectedKeys={[value]}
        onSelectionChange={keys => {
          const [key] = keys === 'all' ? [] : [...keys]
          if (key !== undefined) onChange(String(key) as K)
        }}>
        {options.map(option => <MenuItem key={option.id} id={option.id} textValue={option.label} className={styles.item}>
          {option.icon && <Icon name={option.icon} size={18} />}
          <span lang={option.lang}>{option.label}</span>
          <Icon name="check" size={16} className={styles.check} />
        </MenuItem>)}
      </Menu>
    </Popover>
  </MenuTrigger>
}

/** The language and theme menus of the top bar; both are kept in this browser. */
export function Preferences() {
  const { t, language } = useI18n()
  const theme = useThemePreference()
  return <>
    <OptionsMenu label={t('shell.language', { language: LANGUAGE_NAMES[language] })} icon="globe"
      options={LANGUAGES.map(id => ({ id, label: LANGUAGE_NAMES[id], lang: id }))}
      value={language} onChange={setLanguage} />
    <OptionsMenu label={t('shell.theme', { theme: t(`shell.themes.${theme}`) })} icon={THEME_ICONS[theme]}
      options={THEME_PREFERENCES.map(id => ({ id, label: t(`shell.themes.${id}`), icon: THEME_ICONS[id] }))}
      value={theme} onChange={setThemePreference} />
  </>
}
