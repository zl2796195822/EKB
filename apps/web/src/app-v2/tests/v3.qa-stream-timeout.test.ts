import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClient, type AskStreamHandlers } from '../../lib/api'

/**
 * ApiClient.askStream 防卡死兜底测试：
 * SSE 流式读取在连接挂起（后台挂起 / 网络断开 / TCP 半开）时，reader.read()
 * 既不会 resolve 也不会 reject，导致 UI 永久卡在「正在生成回答」。
 * askStream 现在对「连续无数据块」做超时终止（默认 45s，后端心跳 15s 有 3 倍余量）。
 */

/** 构造一个永不推送数据也永不关闭的 SSE 响应体（模拟连接挂起）。 */
function createNeverEndingResponse(): Response {
  const stream = new ReadableStream<Uint8Array>({
    start() {
      // 不 enqueue、不 close：reader.read() 永久挂起，直到被 cancel
    },
  })
  return new Response(stream, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('ApiClient.askStream 防卡死兜底', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('连接无任何数据块超过阈值时触发 STREAM_IDLE_TIMEOUT，不再永久卡住', async () => {
    const fetchMock = vi.fn(async () => createNeverEndingResponse())
    vi.stubGlobal('fetch', fetchMock)

    const client = new ApiClient('http://test.local')
    client.setToken('token')
    const onError = vi.fn()
    const handlers: AskStreamHandlers = { onError }

    const handlePromise = client.askStream('问题', '', handlers, undefined, undefined, undefined)
    // 让 fetch / assertResponse 完成、读取循环进入挂起状态
    await vi.advanceTimersByTimeAsync(0)
    const handle = await handlePromise
    expect(handle).toBeTruthy()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(onError).not.toHaveBeenCalled()

    // 尚未超过阈值：不触发
    await vi.advanceTimersByTimeAsync(44_000)
    expect(onError).not.toHaveBeenCalled()

    // 超过 45s 仍无任何数据块（含心跳）→ 判定连接已死，按错误收尾
    await vi.advanceTimersByTimeAsync(2_000)
    expect(onError).toHaveBeenCalledTimes(1)
    const payload = onError.mock.calls[0][0] as { code: string }
    expect(payload.code).toBe('STREAM_IDLE_TIMEOUT')
  })

  it('每次收到数据块都会重置无数据计时器，正常慢流不受影响', async () => {
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c
      },
    })
    const fetchMock = vi.fn(async () => new Response(stream, { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    const client = new ApiClient('http://test.local')
    client.setToken('token')
    const onError = vi.fn()
    const handlers: AskStreamHandlers = { onError }

    const handlePromise = client.askStream('问题', '', handlers, undefined, undefined, undefined)
    await vi.advanceTimersByTimeAsync(0)
    await handlePromise

    // 44s 无数据：未触发
    await vi.advanceTimersByTimeAsync(44_000)
    expect(onError).not.toHaveBeenCalled()

    // 推送一个心跳数据块：应重置计时器
    const encoder = new TextEncoder()
    controller!.enqueue(encoder.encode(': heartbeat\n\n'))
    await vi.advanceTimersByTimeAsync(0)
    expect(onError).not.toHaveBeenCalled()

    // 收到数据后又 44s 无数据：仍未触发（计时器已重置）
    await vi.advanceTimersByTimeAsync(44_000)
    expect(onError).not.toHaveBeenCalled()

    // 再超过 45s 无数据：最终触发兜底
    await vi.advanceTimersByTimeAsync(2_000)
    expect(onError).toHaveBeenCalledTimes(1)
  })
})
