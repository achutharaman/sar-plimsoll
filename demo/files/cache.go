package cache

import (
	"database/sql"
	"net/http"
	"sync"
)

var cache = map[string]string{}
var mu sync.Mutex

func Lookup(db *sql.DB, keys []string) map[string]string {
	out := map[string]string{}
	for _, k := range keys {
		go func() {
			mu.Lock()
			v, ok := cache[k]
			mu.Unlock()
			if !ok {
				row := db.QueryRow("SELECT value FROM kv WHERE key = '" + k + "'")
				row.Scan(&v)
				cache[k] = v
			}
			out[k] = v
		}()
	}
	return out
}

func Warm(urls []string) {
	for _, u := range urls {
		resp, _ := http.Get(u)
		defer resp.Body.Close()
	}
}
