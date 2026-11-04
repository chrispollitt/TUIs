// cal.cpp - tvcal's side of tvcal-backend (the co-process) and the date arithmetic
// the views use.  The backend owns everything else about the calendar.
#include "tvcal.h"
#include "coprocess.h"
#include <ctime>

CoProcess &calBackend()
{
    static CoProcess b("tvcal-backend", "TVCAL_BACKEND_LOG", "backend");
    return b;
}

std::vector<std::string> calFields(const std::string &line)
{
    std::vector<std::string> out(1);
    for (size_t i = 0; i < line.size(); ++i)
    {
        char c = line[i];
        if (c == '\t')
            out.emplace_back();
        else if (c == '\\' && i + 1 < line.size())
        {
            char n = line[++i];
            out.back() += n == 't' ? '\t' : n == 'n' ? '\n' : n == 'r' ? '\r' : n;
        }
        else
            out.back() += c;
    }
    return out;
}

bool calAsk(const std::string &req, std::string &body, std::string &err)
{
    long long started = (gTraceLog || gDebugLog) ? traceMicros() : 0;
    int st = -1;
    bool ok = calBackend().call(req, body, st);
    traceSpan("cal", started);
    if (!ok)
    {
        debugLog("cal: no reply to: %s", req.c_str());
        err = "tvcal-backend is not answering (is it installed? see INSTALL.md)";
        return false;
    }
    // the 30 s reminder check and the 3 s request check only when they brought something
    if (gDebugLog && (body.size() || (req != "due" && req != "requests")))
        debugLog("cal: %s -> %d, %lu bytes, %.1f ms", req.substr(0, 200).c_str(), st, (unsigned long)body.size(),
                 (traceMicros() - started) / 1000.0);
    if (st != 0)
    {
        err = body;
        while (!err.empty() && (err.back() == '\n' || err.back() == '\r'))
            err.pop_back();
        if (err.empty())
            err = "tvcal-backend failed";
        return false;
    }
    return true;
}

bool calBackendUp()
{
    std::string body, err;
    return calAsk("ping", body, err) && body == "pong\n";
}

void calBackendStop()
{
    calBackend().stop();
}

// $TVCAL_CONF, else ~/.config/tvcal/tvcal.conf (the backend's rule)
std::string calConfPath()
{
    const char *env = getenv("TVCAL_CONF");
    return env && *env ? std::string(env) : homePath(".config/tvcal/tvcal.conf");
}

// ------------------------------------------------------------------ dates ---
// Days from 1970-01-01 (Howard Hinnant's civil-date algorithms).
static long daysFromCivil(int y, int m, int d)
{
    y -= m <= 2;
    long era = (y >= 0 ? y : y - 399) / 400;
    long yoe = y - era * 400;
    long doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
    long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return era * 146097 + doe - 719468;
}

static Ymd civilFromDays(long z)
{
    z += 719468;
    long era = (z >= 0 ? z : z - 146096) / 146097;
    long doe = z - era * 146097;
    long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long y = yoe + era * 400;
    long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    long mp = (5 * doy + 2) / 153;
    Ymd r;
    r.d = (int)(doy - (153 * mp + 2) / 5 + 1);
    r.m = (int)(mp < 10 ? mp + 3 : mp - 9);
    r.y = (int)(y + (r.m <= 2));
    return r;
}

Ymd ymdToday()
{
    time_t t = time(nullptr);
    struct tm lt;
    localtime_r(&t, &lt);
    Ymd r;
    r.y = lt.tm_year + 1900;
    r.m = lt.tm_mon + 1;
    r.d = lt.tm_mday;
    return r;
}

Ymd ymdAdd(const Ymd &a, int days)
{
    return civilFromDays(daysFromCivil(a.y, a.m, a.d) + days);
}

int ymdDiff(const Ymd &a, const Ymd &b)
{
    return (int)(daysFromCivil(a.y, a.m, a.d) - daysFromCivil(b.y, b.m, b.d));
}

int ymdWeekday(const Ymd &a)
{
    long z = daysFromCivil(a.y, a.m, a.d); // 1970-01-01 was a Thursday
    return (int)(((z % 7) + 7 + 3) % 7);
}

int ymdMonthLength(int y, int m)
{
    static const int len[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    bool leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
    return len[m - 1] + (m == 2 && leap);
}

std::string ymdIso(const Ymd &a)
{
    char b[16];
    snprintf(b, sizeof b, "%04d-%02d-%02d", a.y, a.m, a.d);
    return b;
}

bool ymdParse(const std::string &s, Ymd &out)
{
    int y, m, d;
    if (s.size() != 10 || sscanf(s.c_str(), "%4d-%2d-%2d", &y, &m, &d) != 3 || m < 1 || m > 12 || d < 1 ||
        d > ymdMonthLength(y, m))
        return false;
    out.y = y;
    out.m = m;
    out.d = d;
    return true;
}

Ymd ymdAddMonths(const Ymd &a, int months)
{
    int mi = a.y * 12 + (a.m - 1) + months;
    Ymd r;
    r.y = mi / 12;
    r.m = mi % 12 + 1;
    r.d = std::min(a.d, ymdMonthLength(r.y, r.m));
    return r;
}
