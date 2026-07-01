(function () {
  const log = document.getElementById('chat-log');
  const form = document.getElementById('ask-form');
  const input = document.getElementById('question-input');

  if (!form) return; // no completed jobs — nothing to wire up

  function createBubble(role) {
    const bubble = document.createElement('div');
    bubble.className = 'bubble bubble-' + role;
    log.appendChild(bubble);
    log.scrollTop = log.scrollHeight;
    return bubble;
  }

  function addSources(bubble, sources) {
    if (!sources || !sources.length) return;
    const el = document.createElement('div');
    el.className = 'sources';
    sources.forEach((src) => {
      const chip = document.createElement('span');
      chip.className = 'source-chip';
      chip.textContent = src;
      el.appendChild(chip);
    });
    bubble.appendChild(el);
  }

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;

    const userBubble = createBubble('user');
    userBubble.textContent = question;
    input.value = '';
    input.disabled = true;

    const assistantBubble = createBubble('assistant');
    assistantBubble.classList.add('pending');
    assistantBubble.textContent = 'Thinking…';

    let fullText = '';
    let sources = [];

    try {
      const response = await fetch(apiUrl('/api/v1/ask/stream'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });

      if (!response.ok) {
        assistantBubble.classList.remove('pending');
        assistantBubble.textContent = 'Error: ' + response.statusText;
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        let boundary;
        while ((boundary = buffer.indexOf('\n\n')) !== -1) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);

          let eventName = 'message';
          const dataLines = [];
          for (const line of block.split('\n')) {
            if (line.startsWith('event:')) {
              eventName = line.slice(6).trim();
            } else if (line.startsWith('data:')) {
              const rest = line.slice(5);
              dataLines.push(rest.startsWith(' ') ? rest.slice(1) : rest);
            }
          }

          const data = dataLines.join('\n');

          if (eventName === 'sources') {
            try { sources = JSON.parse(data); } catch (_) {}
          } else if (eventName === 'chunk') {
            if (assistantBubble.classList.contains('pending')) {
              assistantBubble.classList.remove('pending');
              assistantBubble.textContent = '';
            }
            try { fullText += JSON.parse(data); } catch (_) { fullText += data; }
            assistantBubble.textContent = fullText;
            log.scrollTop = log.scrollHeight;
          }
        }
      }

      assistantBubble.classList.remove('pending');
      assistantBubble.innerHTML = marked.parse(fullText || '(No response)');
      addSources(assistantBubble, sources);
      log.scrollTop = log.scrollHeight;
    } catch (err) {
      assistantBubble.classList.remove('pending');
      assistantBubble.textContent = 'Network error: ' + err.message;
    } finally {
      input.disabled = false;
      input.focus();
    }
  });
})();
