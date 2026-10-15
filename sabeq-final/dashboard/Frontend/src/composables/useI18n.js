// Current language (en / ar), the translated text `t`, and text direction (ltr / rtl).
import { computed, ref, watchEffect } from 'vue'
import { messages } from '@/data/translations'
import { fmt, loc } from '@/utils/format'

const lang = ref('en')
const t = computed(() => messages[lang.value] || messages.en)
const dir = computed(() => (lang.value === 'ar' ? 'rtl' : 'ltr'))

watchEffect(() => {
  document.documentElement.lang = lang.value
})

export function useI18n() {
  const setLang = (value) => {
    lang.value = value
  }
  // Backend text {en, ar} in the current language
  const l = (text) => loc(text, lang.value)
  return { lang, t, dir, setLang, l, fmt }
}
