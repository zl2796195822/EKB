const SHA256_CHUNK_BYTES = 1024 * 1024

const INITIAL_STATE = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
  0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
]

const CONSTANTS = [
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]

function rotateRight(value: number, bits: number): number {
  return (value >>> bits) | (value << (32 - bits))
}

function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

class IncrementalSha256 {
  private readonly state = [...INITIAL_STATE]
  private readonly block = new Uint8Array(64)
  private blockLength = 0
  private bitLengthHigh = 0
  private bitLengthLow = 0

  update(input: Uint8Array): void {
    const bits = input.length * 8
    const previousLow = this.bitLengthLow
    this.bitLengthLow = (this.bitLengthLow + bits) >>> 0
    this.bitLengthHigh = (this.bitLengthHigh + Math.floor(bits / 0x1_0000_0000) + (this.bitLengthLow < previousLow ? 1 : 0)) >>> 0

    let offset = 0
    while (offset < input.length) {
      const copyLength = Math.min(64 - this.blockLength, input.length - offset)
      this.block.set(input.subarray(offset, offset + copyLength), this.blockLength)
      this.blockLength += copyLength
      offset += copyLength
      if (this.blockLength === 64) {
        this.processBlock()
        this.blockLength = 0
      }
    }
  }

  digestHex(): string {
    this.block[this.blockLength] = 0x80
    this.blockLength += 1
    if (this.blockLength > 56) {
      this.block.fill(0, this.blockLength)
      this.processBlock()
      this.blockLength = 0
    }
    this.block.fill(0, this.blockLength, 56)
    const view = new DataView(this.block.buffer)
    view.setUint32(56, this.bitLengthHigh, false)
    view.setUint32(60, this.bitLengthLow, false)
    this.processBlock()

    const output = new Uint8Array(32)
    const outputView = new DataView(output.buffer)
    this.state.forEach((word, index) => outputView.setUint32(index * 4, word, false))
    return bytesToHex(output)
  }

  private processBlock(): void {
    const words = new Uint32Array(64)
    const view = new DataView(this.block.buffer)
    for (let index = 0; index < 16; index += 1) words[index] = view.getUint32(index * 4, false)
    for (let index = 16; index < 64; index += 1) {
      const left = words[index - 15]!
      const right = words[index - 2]!
      const sigma0 = rotateRight(left, 7) ^ rotateRight(left, 18) ^ (left >>> 3)
      const sigma1 = rotateRight(right, 17) ^ rotateRight(right, 19) ^ (right >>> 10)
      words[index] = (words[index - 16]! + sigma0 + words[index - 7]! + sigma1) >>> 0
    }

    let [a, b, c, d, e, f, g, h] = this.state
    for (let index = 0; index < 64; index += 1) {
      const sigma1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25)
      const choice = (e & f) ^ (~e & g)
      const temp1 = (h + sigma1 + choice + CONSTANTS[index]! + words[index]!) >>> 0
      const sigma0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22)
      const majority = (a & b) ^ (a & c) ^ (b & c)
      const temp2 = (sigma0 + majority) >>> 0
      h = g
      g = f
      f = e
      e = (d + temp1) >>> 0
      d = c
      c = b
      b = a
      a = (temp1 + temp2) >>> 0
    }
    this.state[0] = (this.state[0]! + a) >>> 0
    this.state[1] = (this.state[1]! + b) >>> 0
    this.state[2] = (this.state[2]! + c) >>> 0
    this.state[3] = (this.state[3]! + d) >>> 0
    this.state[4] = (this.state[4]! + e) >>> 0
    this.state[5] = (this.state[5]! + f) >>> 0
    this.state[6] = (this.state[6]! + g) >>> 0
    this.state[7] = (this.state[7]! + h) >>> 0
  }
}

function fallbackSha256(input: Uint8Array): string {
  const hash = new IncrementalSha256()
  hash.update(input)
  return hash.digestHex()
}

export async function sha256Hex(input: Uint8Array): Promise<string> {
  const subtle = globalThis.crypto?.subtle
  if (subtle && input.buffer instanceof ArrayBuffer) {
    try {
      const source = input.byteOffset === 0 && input.byteLength === input.buffer.byteLength
        ? input.buffer
        : input.buffer.slice(input.byteOffset, input.byteOffset + input.byteLength)
      return bytesToHex(new Uint8Array(await subtle.digest('SHA-256', source)))
    } catch {
      // An unavailable or rejected Web Crypto operation falls through safely.
    }
  }
  return fallbackSha256(input)
}

export async function sha256BlobIncremental(blob: Blob): Promise<string> {
  const hash = new IncrementalSha256()
  let offset = 0
  let chunkCount = 0
  while (offset < blob.size) {
    const end = Math.min(blob.size, offset + SHA256_CHUNK_BYTES)
    hash.update(new Uint8Array(await blob.slice(offset, end).arrayBuffer()))
    offset = end
    chunkCount += 1
    if (chunkCount % 4 === 0 && offset < blob.size) {
      await new Promise<void>((resolve) => globalThis.setTimeout(resolve, 0))
    }
  }
  return hash.digestHex()
}

export async function sha256File(file: File): Promise<string> {
  // Keep file hashing bounded even on HTTPS origins where Web Crypto is
  // available. subtle.digest requires the whole file in memory; the
  // incremental worker keeps directory uploads viable for large files.
  if (typeof Worker === 'undefined') return sha256BlobIncremental(file)

  return new Promise<string>((resolve, reject) => {
    const worker = new Worker(new URL('./sha256.worker.ts', import.meta.url), { type: 'module' })
    const fallBackToMainThread = () => {
      void sha256BlobIncremental(file).then(resolve, reject)
    }
    const handleWorkerFailure = () => {
      worker.terminate()
      fallBackToMainThread()
    }
    worker.onmessage = (event: MessageEvent<{ digest?: string }>) => {
      worker.terminate()
      if (event.data.digest) resolve(event.data.digest)
      else fallBackToMainThread()
    }
    worker.onerror = handleWorkerFailure
    worker.onmessageerror = handleWorkerFailure
    worker.postMessage({ file })
  })
}

export { SHA256_CHUNK_BYTES }
