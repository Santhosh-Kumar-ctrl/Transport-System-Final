"""Seed a demo college (St. Joseph's College of Engineering, Chennai): routes, stops, buses, drivers, students, schedules, today's trips.

    python -m scripts.seed           # seed if empty
    python -m scripts.seed --reset   # wipe every table first

All accounts use the password printed at the end.
"""

import argparse
import asyncio
import random
import sys
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text  # noqa: E402

from app import db_models  # noqa: E402
from app.core.db import SessionLocal, engine  # noqa: E402
from app.core.roles import Role  # noqa: E402
from app.modules.allocation import service as alloc_service  # noqa: E402
from app.modules.allocation.schemas import AssignIn  # noqa: E402
from app.modules.auth import service as auth_service  # noqa: E402
from app.modules.auth.models import User  # noqa: E402
from app.modules.auth.schemas import DriverProfileIn, StudentProfileIn, UserCreate  # noqa: E402
from app.modules.master_data import service as md  # noqa: E402
from app.modules.master_data.schemas import BusIn, RouteIn, RouteStopIn, StopIn  # noqa: E402
from app.modules.trips import service as trips_service  # noqa: E402
from app.modules.trips.models import Direction  # noqa: E402
from app.modules.trips.schemas import ScheduleIn  # noqa: E402

PASSWORD = "transit123"
DOMAIN = "college.edu"

# St. Joseph's College of Engineering, OMR, Semmancheri, Chennai. Stop coordinates are approximate
# (good enough for demo geofences; correct them from the admin app or a phone at the real stop).
# code, name, colour, [(stop name, landmark, minutes from first stop, lat, lng)], campus arrival offset
ROUTES = [
    ("14", "OMR Line", "#0B5CAD", [
        ("Tidel Park", "Tidel Park bus stop, OMR", 0, 12.98930, 80.24840),
        ("Perungudi", "Perungudi toll plaza", 8, 12.96110, 80.24130),
        ("Thoraipakkam", "Thoraipakkam PTC", 15, 12.93720, 80.23380),
        ("Karapakkam", "Karapakkam signal", 22, 12.91600, 80.22900),
        ("Sholinganallur", "Sholinganallur junction", 28, 12.90100, 80.22790),
    ], 40),
    ("7", "ECR Line", "#00857C", [
        ("Adyar Depot", "Opp. Adyar bus depot", 0, 13.00670, 80.25700),
        ("Thiruvanmiyur", "Thiruvanmiyur bus terminus", 8, 12.98300, 80.25940),
        ("Palavakkam", "Palavakkam bus stop, ECR", 15, 12.95800, 80.25600),
        ("Neelankarai", "Neelankarai signal", 19, 12.94900, 80.25500),
        ("Injambakkam", "VGP Golden Beach arch", 26, 12.91900, 80.25100),
    ], 42),
    ("22", "Tambaram Line", "#8C1D40", [
        ("Tambaram East", "Tambaram railway station, east exit", 0, 12.92490, 80.12750),
        ("Selaiyur", "Selaiyur police station", 7, 12.91700, 80.14100),
        ("Sembakkam", "Sembakkam lake bus stop", 13, 12.92150, 80.16100),
        ("Medavakkam", "Medavakkam Koot Road", 20, 12.91850, 80.19250),
        ("Perumbakkam", "Perumbakkam main road", 27, 12.89900, 80.20300),
    ], 38),
]
CAMPUS = ("St. Joseph's College (Campus)", "Main gate, Jeppiaar Nagar, OMR", 12.86970, 80.21860)

BUSES = [("TN09AB1401", 40, "Ashok Leyland Lynx"), ("TN09AB0702", 32, "Tata Starbus"),
         ("TN09AB2203", 50, "Eicher Skyline"), ("TN09AB0904", 20, "Force Traveller")]
DRIVERS = [("Murugan K", "9840011122"), ("Selvi R", "9840033344"), ("Joseph A", "9840055566")]
FIRST = ["Aarav", "Divya", "Arjun", "Meera", "Karthik", "Priya", "Rahul", "Sneha", "Vikram", "Ananya",
         "Harish", "Kavya", "Naveen", "Lakshmi", "Suresh", "Deepa", "Ravi", "Nithya", "Ajay", "Pooja",
         "Gokul", "Swathi", "Pranav", "Keerthana", "Siddharth", "Janani", "Manoj", "Revathi", "Vishnu", "Aishwarya"]
LAST = ["S", "R", "K", "M", "P", "V", "N", "T", "B", "G"]
DEPTS = ["CSE", "ECE", "MECH", "CIVIL", "IT", "EEE"]


async def check_schema() -> None:
    """Stop before touching anything if the database is behind the code's migrations (after a
    `git pull` that added one), instead of failing halfway with a missing column."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    async with engine.connect() as conn:
        try:
            current = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        except Exception:  # noqa: BLE001 - no alembic_version table: never migrated
            current = None
    if current != head:
        await engine.dispose()
        sys.exit(f"The database is at migration {current or '(none)'}, but the code needs {head}.\n"
                 "Run `alembic upgrade head` (in backend/) first, then seed again.")


async def reset() -> None:
    tables = ", ".join(t.name for t in db_models.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    print("All tables truncated.")


async def seed() -> None:
    random.seed(7)
    async with SessionLocal() as s:
        if await s.scalar(select(User.id).where(User.email == f"admin@{DOMAIN}")):
            print("Already seeded (admin exists). Use --reset to start over.")
            return

        admin = await auth_service.create_user(s, UserCreate(
            email=f"admin@{DOMAIN}", password=PASSWORD, full_name="Transport Office", role=Role.ADMIN,
            phone="04422220000"), actor_id=None)

        campus = await md.create_stop(s, StopIn(name=CAMPUS[0], landmark=CAMPUS[1],
                                                latitude=CAMPUS[2], longitude=CAMPUS[3]))
        routes = []
        for code, name, color, stops, campus_offset in ROUTES:
            route = await md.create_route(s, RouteIn(code=code, name=name, color=color), actor_id=admin.id)
            items = []
            for stop_name, landmark, offset, lat, lng in stops:
                stop = await md.create_stop(s, StopIn(name=stop_name, landmark=landmark, latitude=lat, longitude=lng))
                items.append(RouteStopIn(stop_id=stop.id, offset_min=offset))
            items.append(RouteStopIn(stop_id=campus.id, offset_min=campus_offset))
            routes.append(await md.set_route_stops(s, route.id, items, actor_id=admin.id))

        buses = [await md.create_bus(s, BusIn(registration_no=r, capacity=c, model=m), actor_id=admin.id)
                 for r, c, m in BUSES]
        drivers = []
        for i, (name, phone) in enumerate(DRIVERS, start=1):
            drivers.append(await auth_service.create_user(s, UserCreate(
                email=f"driver{i}@{DOMAIN}", password=PASSWORD, full_name=name, phone=phone, role=Role.DRIVER,
                driver=DriverProfileIn(license_no=f"TN09 2019{i:07d}")), actor_id=admin.id))

        # each of the first three buses gets its regular driver (the fourth is a spare)
        for bus, driver in zip(buses, drivers):
            await md.assign_driver(s, bus.id, driver.id, actor_id=admin.id)

        # pickup (morning) + drop (evening) for each route, every weekday + Saturday
        departures = [(time(7, 20), time(16, 30)), (time(7, 35), time(16, 40)), (time(7, 10), time(16, 35))]
        for route, bus, driver, (am, pm) in zip(routes, buses, drivers, departures):
            for direction, t in ((Direction.PICKUP, am), (Direction.DROP, pm)):
                await trips_service.create_schedule(s, ScheduleIn(
                    route_id=route.id, bus_id=bus.id, driver_id=driver.id, direction=direction,
                    departure_time=t, days_of_week=[1, 2, 3, 4, 5, 6]), actor_id=admin.id)

        students = []
        for i, first in enumerate(FIRST, start=1):
            students.append(await auth_service.create_user(s, UserCreate(
                email=f"student{i}@{DOMAIN}", password=PASSWORD, full_name=f"{first} {random.choice(LAST)}",
                phone=f"98{random.randint(10000000, 99999999)}", role=Role.STUDENT,
                student=StudentProfileIn(roll_no=f"22{random.choice(DEPTS)}{i:03d}",
                                         department=random.choice(DEPTS), year=random.randint(1, 4))),
                actor_id=admin.id))
        await s.flush()

        # allocate 26 of 30 students (a few stay unallocated for the admin to assign)
        for idx, student in enumerate(students[:26]):
            route = routes[idx % len(routes)]
            pickup_stops = route.stops[:-1]  # not the campus itself
            stop = pickup_stops[idx // len(routes) % len(pickup_stops)]
            await alloc_service.assign(s, AssignIn(student_id=student.id, route_id=route.id, stop_id=stop.stop_id),
                                       actor_id=admin.id)

        gen = await trips_service.generate_trips(s, actor_id=admin.id)
        await s.commit()

    print(f"Seeded {len(ROUTES)} routes, {len(BUSES)} buses, {len(DRIVERS)} drivers, {len(FIRST)} students; "
          f"{gen.created} trips generated for {gen.service_date}.")
    print("\nLogins (password for all: %s)" % PASSWORD)
    print(f"  admin     admin@{DOMAIN}")
    print(f"  drivers   driver1@{DOMAIN} .. driver{len(DRIVERS)}@{DOMAIN}   (driver1 runs route 14)")
    print(f"  students  student1@{DOMAIN} .. student{len(FIRST)}@{DOMAIN}  (student1 rides route 14)")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="truncate all tables first")
    args = parser.parse_args()
    await check_schema()
    if args.reset:
        await reset()
    await seed()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
