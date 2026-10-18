<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'

withDefaults(defineProps<{ text?: string }>(), { text: '暂无数据' })
</script>

<template>
  <div class="empty-state">
    <Inbox :size="42" stroke-width="1.4" />
    <span>{{ text }}</span>
  </div>
</template>

