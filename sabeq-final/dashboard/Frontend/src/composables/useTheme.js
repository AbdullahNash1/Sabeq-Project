// Light / dark theme. Shared by the whole app; writes data-theme on <html> so tokens.css switches colours.
import { ref, watchEffect } from 'vue'

const theme = ref('light')

watchEffect(() => {
  document.documentElement.dataset.theme = theme.value
})

export function useTheme() {
  const setTheme = (value) => {
    theme.value = value
  }
  return { theme, setTheme }
}
