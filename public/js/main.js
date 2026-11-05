const PRICES = {
  'cassava-peels': 15000, 'rice-husks': 12000, 'spent-grains': 18000, 'crop-stalks': 10000
}; // display only. Keep in sync with server.js; the server is the source of truth.

const form = document.getElementById('order-form');
const totalEl = document.getElementById('total');
const msg = document.getElementById('form-msg');
const btn = form.querySelector('button[type=submit]');

function updateTotal() {
  const t = Number(form.tonnes.value) || 0;
  const p = PRICES[form.product.value] || 0;
  totalEl.textContent = '₦' + (t * p).toLocaleString('en-NG');
}
form.addEventListener('input', updateTotal);
updateTotal();

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  msg.textContent = '';
  btn.disabled = true;
  btn.textContent = 'Opening Paystack…';
  try {
    const res = await fetch('/api/pay', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(new FormData(form)))
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    window.location.href = data.url;
  } catch (err) {
    msg.textContent = err.message || 'Could not start payment.';
    btn.disabled = false;
    btn.textContent = 'Pay with Paystack';
  }
});

document.querySelectorAll('[data-product]').forEach((b) =>
  b.addEventListener('click', () => { form.product.value = b.dataset.product; updateTotal(); })
);
