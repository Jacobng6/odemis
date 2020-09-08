from odemis import model
sem = model.getComponent(role="SEM")

dwell_time = sem.get_dwell_time()
print("Dwell time is: {} seconds".format(dwell_time))

new_dwell_time = input("New dwell time in seconds: ")
sem.set_dwell_time(new_dwell_time)
dwell_time = sem.get_dwell_time()
print("Dwell time is now: {} seconds".format(dwell_time))

info = dwell_time_info()
print(info)