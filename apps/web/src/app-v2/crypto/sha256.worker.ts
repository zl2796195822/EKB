import { sha256BlobIncremental } from './sha256'

self.onmessage = (event: MessageEvent<{ file?: Blob }>) => {
  const file = event.data.file
  if (!file) {
    self.postMessage({ digest: null })
    return
  }
  void sha256BlobIncremental(file)
    .then((digest) => self.postMessage({ digest }))
    .catch(() => self.postMessage({ digest: null }))
}
