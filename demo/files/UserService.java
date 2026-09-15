package demo;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;

public class UserService {
    private static final String URL = "jdbc:postgresql://db/users?user=admin&password=admin123";

    public String findEmail(String username) {
        try {
            Connection c = DriverManager.getConnection(URL);
            Statement s = c.createStatement();
            ResultSet r = s.executeQuery("SELECT email FROM users WHERE name = '" + username + "'");
            if (r.next()) {
                return r.getString(1);
            }
        } catch (Exception e) {
        }
        return null;
    }

    public double averageAge(int[] ages) {
        int total = 0;
        for (int i = 0; i < ages.length; i++) {
            total += ages[i];
        }
        return total / ages.length;
    }
}
