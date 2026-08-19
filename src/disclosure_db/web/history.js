export const STORE_VERSION = 1;
export const MAX_CONVERSATIONS = 50;
export const MAX_MESSAGES = 100;

export function emptyStore() {
  return {version: STORE_VERSION, conversations: []};
}

function normalizeText(value) {
  return value.trim().replace(/\s+/g, " ");
}

function isMessage(value) {
  return Boolean(
    value
      && typeof value === "object"
      && (value.role === "user" || value.role === "assistant")
      && typeof value.text === "string"
      && typeof value.created_at === "string",
  );
}

function isConversation(value) {
  return Boolean(
    value
      && typeof value === "object"
      && typeof value.id === "string"
      && typeof value.title === "string"
      && typeof value.created_at === "string"
      && typeof value.updated_at === "string"
      && Array.isArray(value.messages)
      && value.messages.every(isMessage),
  );
}

function newestFirst(conversations) {
  return conversations.toSorted((left, right) => right.updated_at.localeCompare(left.updated_at));
}

export function normalizeStore(value) {
  if (
    !value
    || typeof value !== "object"
    || value.version !== STORE_VERSION
    || !Array.isArray(value.conversations)
    || !value.conversations.every(isConversation)
  ) {
    return emptyStore();
  }

  const conversations = value.conversations.map((conversation) => ({
    ...conversation,
    messages: conversation.messages.slice(-MAX_MESSAGES).map((message) => ({...message})),
  }));
  return {
    version: STORE_VERSION,
    conversations: newestFirst(conversations).slice(0, MAX_CONVERSATIONS),
  };
}

export function createConversation(store, question, now, id) {
  const normalized = normalizeStore(store);
  const text = normalizeText(question);
  const conversation = {
    id,
    title: text.slice(0, 40),
    created_at: now,
    updated_at: now,
    messages: [{role: "user", text, created_at: now}],
  };
  return normalizeStore({
    version: STORE_VERSION,
    conversations: [conversation, ...normalized.conversations],
  });
}

export function appendMessage(store, conversationId, message) {
  const normalized = normalizeStore(store);
  if (!isMessage(message)) {
    return normalized;
  }

  const conversations = normalized.conversations.map((conversation) => {
    if (conversation.id !== conversationId) {
      return conversation;
    }
    return {
      ...conversation,
      updated_at: message.created_at,
      messages: [...conversation.messages, {...message}].slice(-MAX_MESSAGES),
    };
  });
  return normalizeStore({version: STORE_VERSION, conversations});
}

export function searchConversations(store, query) {
  const normalized = normalizeStore(store);
  const needle = normalizeText(query).toLocaleLowerCase();
  if (!needle) {
    return normalized.conversations;
  }
  return normalized.conversations.filter((conversation) => {
    const searchable = [
      conversation.title,
      ...conversation.messages.map((message) => message.text),
    ].join("\n").toLocaleLowerCase();
    return searchable.includes(needle);
  });
}

export function deleteConversation(store, conversationId) {
  const normalized = normalizeStore(store);
  return {
    version: STORE_VERSION,
    conversations: normalized.conversations.filter(({id}) => id !== conversationId),
  };
}

export function clearConversations() {
  return emptyStore();
}
