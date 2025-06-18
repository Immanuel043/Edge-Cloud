import * as tus from 'tus-js-client';

function uploadFile(file) {
  const upload = new tus.Upload(file, {
    endpoint: 'http://localhost:1080/files/', // tusd endpoint
    retryDelays: [0, 1000, 3000, 5000],
    metadata: {
      filename: file.name,
      filetype: file.type
    },
    onError: function (error) {
      console.error("❌ Upload failed:", error);
    },
    onProgress: function (bytesUploaded, bytesTotal) {
      const percentage = ((bytesUploaded / bytesTotal) * 100).toFixed(2);
      console.log(`${percentage}% uploaded (${bytesUploaded}/${bytesTotal})`);
    },
    onSuccess: function () {
      console.log("✅ Upload complete:", upload.url);
    }
  });

  upload.start();
}
