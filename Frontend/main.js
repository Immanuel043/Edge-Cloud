import { Upload } from 'tus-js-client';

document.getElementById('theme-toggle').onclick = () => {
  document.documentElement.classList.toggle('dark');
};

document.getElementById('upload').onclick = () => {
  const file = document.getElementById('file').files[0];
  if (!file) return;

  const upload = new Upload(file, {
    endpoint: 'http://localhost:8000/files/',
    chunkSize: 50 * 1024 * 1024, // 50MB chunks
    parallelUploads: 4,
    retryDelays: [0, 1000, 3000, 5000],
    metadata: { filename: file.name },
    onError: error => {
      document.getElementById('progress').innerText = '❌ ' + error.message;
    },
    onProgress: (bytesSent, bytesTotal) => {
      const percentage = ((bytesSent / bytesTotal) * 100).toFixed(2);
      document.getElementById('progress').innerText = `Uploading... ${percentage}%`;
    },
    onSuccess: () => {
      document.getElementById('progress').innerText = '✅ Upload complete!';
    }
  });

  upload.start();
};