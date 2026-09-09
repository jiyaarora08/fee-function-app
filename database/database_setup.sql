-- This script uses GO batch separators and must be run with a tool that supports batches (SSMS, Azure Data Studio, sqlcmd, or the VS Code mssql extension) — it will not run via a raw pymssql/pyodbc cursor.execute() call.

/*
  Fee Management System — Azure SQL setup script

  Purpose:
    Create the Students and Administrators tables used by the Azure Functions
    application, add the DueDate index, and load sample data for a fresh
    non-production database.

  Safety:
    This script does not DROP DATABASE, DROP TABLE, or otherwise reset
    existing production data. It only creates objects when they do not
    already exist, and inserts sample rows only when the tables are empty.

  Do not run this against the live production database unless you have
  explicitly confirmed the objects are missing and the environment is safe.

  Notes:
    - SQL Server requires a length on NVARCHAR; lengths below are chosen
      to match typical name/course/email values used by the application.
    - Column names match the Python queries in function_app.py:
      Students: StudentID, Name, Course, Email, TotalFee, PaidAmount, DueDate
      Administrators: AdminID, Name, Role
    - Email values are synthetic placeholders only.
    - Sample student rows are representative seed data for the assignment
      (20 students). They are not an export of the live Azure database.
*/

/* ------------------------------------------------------------------ */
/* Students                                                            */
/* ------------------------------------------------------------------ */

IF OBJECT_ID(N'dbo.Students', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.Students
    (
        StudentID   INT            NOT NULL PRIMARY KEY,
        Name        NVARCHAR(100)  NOT NULL,
        Course      NVARCHAR(100)  NOT NULL,
        Email       NVARCHAR(255)  NOT NULL,
        TotalFee    DECIMAL(12, 2) NOT NULL,
        PaidAmount  DECIMAL(12, 2) NOT NULL CONSTRAINT DF_Students_PaidAmount DEFAULT (0),
        DueDate     DATE           NOT NULL
    );
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = N'idx_duedate'
      AND object_id = OBJECT_ID(N'dbo.Students')
)
BEGIN
    CREATE INDEX idx_duedate ON dbo.Students (DueDate);
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.Students)
BEGIN
    INSERT INTO dbo.Students
        (StudentID, Name, Course, Email, TotalFee, PaidAmount, DueDate)
    VALUES
        (1,  N'Aarav Mehta',      N'B.Tech CSE',      N'student01@example.com', 120000.00,    700.00, '2026-08-08'),
        (2,  N'Isha Kapoor',      N'B.Tech ECE',      N'student02@example.com', 110000.00,  50000.00, '2026-08-28'),
        (3,  N'Rohan Gupta',      N'BBA',             N'student03@example.com',  90000.00,      0.00, '2026-09-02'),
        (4,  N'Ananya Singh',     N'MBA Finance',     N'student04@example.com', 150000.00, 150000.00, '2026-09-27'),
        (5,  N'Vivaan Joshi',     N'B.Sc IT',         N'student05@example.com',  95000.00,  40000.00, '2026-09-05'),
        (6,  N'Diya Reddy',       N'B.Com',           N'student06@example.com',  85000.00,  85000.00, '2026-09-22'),
        (7,  N'Kabir Nair',       N'B.Tech Mech',     N'student07@example.com', 125000.00,  60000.00, '2026-08-23'),
        (8,  N'Saanvi Iyer',      N'BA Economics',    N'student08@example.com',  80000.00,      0.00, '2026-09-06'),
        (9,  N'Aditya Rao',       N'B.Tech CSE',      N'student09@example.com', 120000.00,  20000.00, '2026-08-18'),
        (10, N'Myra Chatterjee',  N'MBA HR',          N'student10@example.com', 140000.00, 140000.00, '2026-09-17'),
        (11, N'Arjun Malhotra',   N'B.Tech ECE',      N'student11@example.com', 110000.00,  55000.00, '2026-09-12'),
        (12, N'Pihu Desai',       N'B.Sc IT',         N'student12@example.com',  95000.00,  95000.00, '2026-10-02'),
        (13, N'Reyansh Pillai',   N'BBA',             N'student13@example.com',  90000.00,  30000.00, '2026-08-30'),
        (14, N'Anika Bhatt',      N'B.Com',           N'student14@example.com',  85000.00,      0.00, '2026-08-26'),
        (15, N'Vihaan Shah',      N'B.Tech Mech',     N'student15@example.com', 125000.00, 125000.00, '2026-10-07'),
        (16, N'Navya Menon',      N'BA Economics',    N'student16@example.com',  80000.00,  40000.00, '2026-09-10'),
        (17, N'Advait Kulkarni',  N'MBA Finance',     N'student17@example.com', 150000.00,  75000.00, '2026-08-13'),
        (18, N'Ira Bose',         N'B.Tech CSE',      N'student18@example.com', 120000.00, 120000.00, '2026-09-25'),
        (19, N'Yuvaan Trivedi',   N'B.Sc IT',         N'student19@example.com',  95000.00,      0.00, '2026-09-04'),
        (20, N'Kiara Sen',        N'MBA HR',          N'student20@example.com', 140000.00,  70000.00, '2026-08-20');
END
GO

/* ------------------------------------------------------------------ */
/* Administrators                                                      */
/* The Python application authorizes updates via Microsoft Entra ID    */
/* application role FeeAdmin, not via this table. The table is part of */
/* the assignment schema and is included for completeness.             */
/* ------------------------------------------------------------------ */

IF OBJECT_ID(N'dbo.Administrators', N'U') IS NULL
BEGIN
    CREATE TABLE dbo.Administrators
    (
        AdminID INT           NOT NULL PRIMARY KEY,
        Name    NVARCHAR(100) NOT NULL,
        Role    NVARCHAR(50)  NOT NULL
    );
END
GO

IF NOT EXISTS (SELECT 1 FROM dbo.Administrators)
BEGIN
    INSERT INTO dbo.Administrators (AdminID, Name, Role)
    VALUES
        (1, N'Priya Sharma', N'SuperAdmin'),
        (2, N'Rahul Verma',  N'FeeManager');
END
GO
