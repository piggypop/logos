# code · sql query

**Model:** gemma3:12b
**Category:** code
**Passed:** ✅
**Elapsed:** 0.0s

## Response

Εντάξει, εδώ είναι ένα SQL query που βρίσκει τους 5 πελάτες με τις περισσότερες παραγγελίες από έναν πίνακα `orders`:

```sql
SELECT customer_id, COUNT(*) AS order_count
FROM orders
GROUP BY customer_id
ORDER BY order_count DESC
LIMIT 5;
```

**Επεξήγηση:**

*   `SELECT customer_id, COUNT(*) AS order_count`:  Επιλέγει το `customer_id` και μετράει τον αριθμό των παραγγελιών για κάθε πελάτη, ονομάζοντας αυτή την μετρική `order_count`.
*   `FROM orders`:  Καθορίζει τον πίνακα `orders` ως την πηγή των δεδομένων.
*   `GROUP BY customer_id`: Ομαδοποιεί τις παραγγελίες ανά `customer_id`, ώστε να μετρηθεί ο αριθμός των παραγγελιών για κάθε πελάτη ξεχωριστά.
*   `ORDER BY order_count DESC`: Ταξινομεί τα αποτελέσματα κατά `order_count` σε φθίνουσα σειρά (από το μεγαλύτερο στο μικρότερο).
*   `LIMIT 5`:  Περιορίζει τα αποτελέσματα στους 5 πρώτους πελάτες (δηλαδή, τους 5 πελάτες με τις περισσότερες παραγγελίες).

Αυτό το query θα επιστρέψει δύο στήλες: `customer_id` και `order_count`, όπου `order_count` αντιπροσωπεύει τον αριθμό των παραγγελιών για κάθε πελάτη.

