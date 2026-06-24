# 足球引擎容器(plan 03§7)。Node 薄壳 server.js + lib/。arm64(DGX Spark)。
FROM node:20-slim
WORKDIR /app
COPY package*.json ./
RUN npm install --omit=dev 2>/dev/null || npm install --production 2>/dev/null || true
COPY . .
EXPOSE 7000
CMD ["node", "server.js"]
