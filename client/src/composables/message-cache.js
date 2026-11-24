export function mergeMessage(cache, message) {
  const id = message.conversation_id
  const bucket = cache[id] ?? (cache[id] = Object.create(null))
  const fresh = !Object.hasOwn(bucket, message.message_id)
  bucket[message.message_id] = message
  return fresh
}
