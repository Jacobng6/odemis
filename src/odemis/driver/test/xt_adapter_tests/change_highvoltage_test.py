from odemis import model
sem = model.getComponent(role="SEM")

ht_voltage = sem.get_ht_voltage()
print("High voltage is: {} volt".format(ht_voltage))

new_ht_voltage = input("New high voltage in volt: ")
sem.set_ht_voltage(new_ht_voltage)
ht_voltage = sem.get_ht_voltage()
print("High voltage is now: {} volt".format(ht_voltage))

info = ht_voltage_info()
print(info)