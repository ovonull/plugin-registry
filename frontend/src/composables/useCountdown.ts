import { onBeforeUnmount, ref } from 'vue'

export function useCountdown() {
  const countdown = ref(0)
  let timer: number | undefined

  function startCountdown(seconds = 60) {
    countdown.value = seconds
    if (timer) window.clearInterval(timer)
    timer = window.setInterval(() => {
      countdown.value -= 1
      if (countdown.value <= 0 && timer) {
        window.clearInterval(timer)
        timer = undefined
      }
    }, 1000)
  }

  onBeforeUnmount(() => { if (timer) window.clearInterval(timer) })
  return { countdown, startCountdown }
}
